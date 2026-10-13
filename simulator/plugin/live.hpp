/** Physical roller conveyor lifecycle and bounded, measured ROS2 world observations. */
#pragma once
#include <chrono>
#include <iomanip>
#include <memory>
#include <sstream>
#include <random>

namespace gazebo {
class LiveFactoryController {
 public:
  /** Bind each physical slot and roller; construct a new epoch for every server start. */
  LiveFactoryController(physics::WorldPtr world, gazebo_ros::Node::SharedPtr ros, sdf::ElementPtr sdf)
      : world_(world), ros_(ros) {
    version_ = sdf->Get<std::string>("world_version");
    defectMode_ = sdf->Get<std::string>("defect_mode");
    level_ = sdf->Get<double>("initial_fault_level");
    motorOmega_=sdf->Get<double>("diagnostic_motor_rpm")*2.0*std::acos(-1.0)/60.0;
    rng_.seed(sdf->Get<unsigned int>("seed"));
    transport_ = transport::NodePtr(new transport::Node()); transport_->Init(world_->Name());
    visual_ = transport_->Advertise<msgs::Visual>("~/visual");
    auto now = std::chrono::system_clock::now().time_since_epoch();
    run_ = "factory-"+std::to_string(std::chrono::duration_cast<std::chrono::microseconds>(now).count());
    for (int i=0; i<sdf->Get<int>("roller_count"); ++i) {
      std::ostringstream name; name << "roller_" << std::setw(2) << std::setfill('0') << i;
      rollerNames_.push_back(name.str());
    }
    for (int i=0; i<sdf->Get<int>("product_count"); ++i) {
      std::ostringstream name; name << "product_" << std::setw(2) << std::setfill('0') << i+1;
      Product product; product.slot=name.str(); product.serial=NewSerial();
      product.defectClass=SampleClass(); products_.push_back(product);
    }
    state_ = ros_->create_publisher<std_msgs::msg::String>("/factory/world/state", 10);
    event_ = ros_->create_publisher<std_msgs::msg::String>("/factory/inspection/event", 10);
    reference_ = ros_->create_publisher<std_msgs::msg::String>("/factory/private/defect_reference", 10);
    joints_ = ros_->create_publisher<sensor_msgs::msg::JointState>("/factory/motor_state", 10);
    command_ = ros_->create_subscription<std_msgs::msg::String>("/factory/scene", 10,
      [this](std_msgs::msg::String::SharedPtr message) {
        std::lock_guard<std::mutex> lock(mutex_); pending_.push_back(message->data);
      });
  }

  /** Apply actuator commands and lifecycle changes only on Gazebo's physics thread. */
  void Update() {
    auto motor=world_->ModelByName("motor");
    if (!motor || !world_->ModelByName(rollerNames_.front())) return;
    const double time=world_->SimTime().Double();
    Commands();
    if (!actuatorsConfigured_) {
      std::vector<std::string> names=rollerNames_; names.push_back("motor");
      for (const auto &name:names) {
        auto model=world_->ModelByName(name); if (!model) return;
        auto body=boost::dynamic_pointer_cast<physics::ODELink>(model->GetLink("shaft"));
        if (!body || !body->GetODEId()) return;
        if (name=="motor" || name==rollerNames_.front())
          gzmsg << "live ODE initial " << name << " maxAngular=" << dBodyGetMaxAngularSpeed(body->GetODEId())
            << " angularDamping=" << dBodyGetAngularDamping(body->GetODEId())
            << " linearDamping=" << dBodyGetLinearDamping(body->GetODEId()) << "\n";
        // Disable post-step velocity decay on anchored drive shafts. Torque still
        // acts through ODE joint motors and plate motion still comes from contacts.
        dBodySetAngularDamping(body->GetODEId(),0.0); dBodySetLinearDamping(body->GetODEId(),0.0);
        dBodySetMaxAngularSpeed(body->GetODEId(),1000.0);
        model->GetJoint("shaft_joint")->SetDamping(0,0.0);
        model->GetJoint("shaft_joint")->SetVelocityLimit(0,1000.0);
      }
      actuatorsConfigured_=true;
    }
    if (time<3.0 && time-lastVisual_>=.25) {
      for (const auto &product:products_) SelectVisuals(product);
      lastVisual_=time;
    }
    auto motorJoint=motor->GetJoint("shaft_joint");
    if (!diagnostics_ && time>=1.0) {
      auto shaft=boost::dynamic_pointer_cast<physics::ODELink>(motor->GetLink("shaft"));
      auto rollerShaft=boost::dynamic_pointer_cast<physics::ODELink>(world_->ModelByName(rollerNames_.front())->GetLink("shaft"));
      for (const auto &body: {shaft,rollerShaft}) if (body && body->GetODEId()) {
        gzmsg << "live ODE diagnostics " << body->GetScopedName()
          << " maxAngular=" << dBodyGetMaxAngularSpeed(body->GetODEId())
          << " angularDamping=" << dBodyGetAngularDamping(body->GetODEId())
          << " linearDamping=" << dBodyGetLinearDamping(body->GetODEId()) << "\n";
      }
      gzmsg << "live joint diagnostics motor observed=" << motorJoint->GetVelocity(0)
        << " velocityLimit=" << motorJoint->GetVelocityLimit(0) << "\n";
      diagnostics_=true;
    }
    motorJoint->SetParam("fmax",0,100.0); motorJoint->SetParam("vel",0,motorOmega_);
    for (const auto &name:rollerNames_) {
      auto model=world_->ModelByName(name); if (!model) continue;
      auto joint=model->GetJoint("shaft_joint");
      joint->SetParam("fmax",0,20.0); joint->SetParam("vel",0,(running_ && active_<0) ? 6.0 : 0.0);
    }
    for (size_t i=0; i<products_.size(); ++i) {
      auto &product=products_[i]; auto model=world_->ModelByName(product.slot); if (!model) continue;
      auto pose=model->WorldPose(); auto velocity=model->WorldLinearVel();
      const double x=pose.Pos().X();
      if (!product.inspected && active_<0 && running_ && x>=-.085 && x<.30) {
        active_=static_cast<int>(i); product.inspection=run_+"-I"+std::to_string(++inspectionCounter_);
        product.holdTime=time; product.readyTime=-1; product.stage="inspection";
        Emit(product,"entered");
      }
      if (active_==static_cast<int>(i) && product.readyTime<0 && running_ && time-product.holdTime>=.40 && velocity.Length()<.03) {
        product.readyTime=time; Emit(product,"ready");
      }
      if (product.inspected && x>=.75 && !product.exited) {
        product.stage="sorting";
        if (product.classification!="unknown" && running_ && active_<0) {
          const double target=product.classification=="defective" ? .55 : -.55;
          // A force-actuated side pusher changes physical position; it never teleports a plate.
          const double force=std::clamp(40.0*(target-pose.Pos().Y())-7.5*velocity.Y(),-12.0,12.0);
          model->GetLink("body")->AddForce(ignition::math::Vector3d(0,force,0));
        }
      }
      if (x>=2.05 && !product.exited) {
        product.exited=true; product.stage="outfeed";
        product.outfeedLane=product.classification=="unknown" ? "unknown" :
          (pose.Pos().Y()>.30 ? "reject" : (pose.Pos().Y()<-.30 ? "normal" : "unconfirmed"));
        Emit(product,"outfeed");
      }
      if (x>=2.35 && product.exited && ClearInfeed(static_cast<int>(i))) {
        // Outfeed replenishment is explicitly a lifecycle reset, never claimed as physical transport.
        model->SetWorldPose(ignition::math::Pose3d(-10.8,0,.585,0,0,0));
        model->SetLinearVel(ignition::math::Vector3d::Zero); model->SetAngularVel(ignition::math::Vector3d::Zero);
        product.serial=NewSerial(); product.inspection=""; product.inspected=false; product.exited=false;
        product.defectClass=SampleClass(); SelectVisuals(product);
        product.classification="unknown"; product.outfeedLane="unknown"; product.stage="infeed";
        ++resets_; Emit(product,"infeed");
      } else if (!product.inspected && active_!=static_cast<int>(i)) {
        product.stage=x < -9.7 ? "infeed" : "transport";
      }
    }
    if (time-lastJoint_>=.02) {
      auto roller=world_->ModelByName(rollerNames_.front())->GetJoint("shaft_joint");
      sensor_msgs::msg::JointState message; message.header.stamp.sec=world_->SimTime().sec;
      message.header.stamp.nanosec=world_->SimTime().nsec;
      message.name={"motor_shaft","conveyor_roller"};
      message.position={motorJoint->Position(0),roller->Position(0)};
      message.velocity={motorJoint->GetVelocity(0),roller->GetVelocity(0)};
      message.effort={motorJoint->GetForce(0),roller->GetForce(0)};
      joints_->publish(message); lastJoint_=time;
    }
    const double wall=std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    if (wall-lastState_>=.090) { PublishState(); lastState_=wall; }
  }

 private:
  struct Product {
    std::string slot,serial,inspection,stage="infeed",classification="unknown",outfeedLane="unknown";
    bool inspected=false,exited=false;
    double holdTime=0,readyTime=-1;
    int defectClass=-1;
  };

  /** Allocate a logical product serial independently of the reusable physical body. */
  std::string NewSerial() { return run_+"-P"+std::to_string(++productCounter_); }

  /** Keep operational defect probability identical to the baseline fault-dependent relation. */
  int SampleClass() {
    if (defectMode_=="balanced-evaluation") return static_cast<int>((productCounter_-1)%4)-1;
    const double probability=.02+.085*level_;
    if (std::uniform_real_distribution<double>(0,1)(rng_)>=probability) return -1;
    return std::uniform_int_distribution<int>(0,2)(rng_);
  }

  /** Apply actual 3D visual visibility only for a freshly replenished logical product. */
  void SelectVisuals(const Product &product) {
    for (int kind=0;kind<3;++kind) {
      const int count=kind==1 ? 2 : 3;
      for (int index=0;index<count;++index) {
        const std::string parent=product.slot+"::body";
        msgs::Visual message; message.set_name(parent+"::defect_"+std::to_string(kind)+"_"+std::to_string(index));
        message.set_parent_name(parent); message.set_visible(kind==product.defectClass);
        message.set_transparency(kind==product.defectClass ? 0.0 : 1.0);
        visual_->Publish(message,true); visual_->SendMessage();
      }
    }
  }

  /** Ensure a plate cannot be replenished on top of another plate at the infeed. */
  bool ClearInfeed(int index) {
    for (size_t i=0;i<products_.size();++i) {
      if (static_cast<int>(i)==index) continue;
      auto model=world_->ModelByName(products_[i].slot);
      if (model && std::abs(model->WorldPose().Pos().X()+10.8)<1.02) return false;
    }
    return true;
  }

  /** Process classification only when its epoch and exact logical inspection match. */
  void Commands() {
    std::deque<std::string> commands;
    { std::lock_guard<std::mutex> lock(mutex_); commands.swap(pending_); }
    for (const auto &text:commands) {
      try {
        auto message=nlohmann::json::parse(text);
        if (message.contains("running") && message["running"].is_boolean()) running_=message["running"];
        if (message.contains("fault_level") && message["fault_level"].is_number())
          level_=std::clamp(message["fault_level"].get<double>(),0.0,10.0);
        if (message.contains("inspection_complete") && active_>=0) {
          auto &product=products_[active_];
          if (message["inspection_complete"]==product.inspection && running_) {
            product.inspected=true; product.stage="inspected"; Emit(product,"captured"); active_=-1;
          }
        }
        if (message.contains("quality") && message["quality"].value("run_id","")==run_) {
          const auto &quality=message["quality"];
          for (auto &product:products_) {
            if (quality.value("product_id","")==product.serial && quality.value("inspection_id","")==product.inspection) {
              product.classification=quality.value("defective",false) ? "defective" : "normal";
              Emit(product,"classified");
            }
          }
        }
      } catch (const std::exception &error) { gzerr << "live command: " << error.what() << "\n"; }
    }
  }

  /** Emit provenance and observed poses, omitting any generated defect reference label. */
  void Emit(const Product &product,const std::string &phase) {
    auto model=world_->ModelByName(product.slot); auto pose=model->WorldPose();
    nlohmann::json value={{"run_id",run_},{"world_version",version_},{"phase",phase},
      {"product_id",product.serial},{"inspection_id",product.inspection},{"slot_id",product.slot},
      {"sim_time",world_->SimTime().Double()},{"classification",product.classification},
      {"outfeed_lane",product.outfeedLane},{"position",{pose.Pos().X(),pose.Pos().Y(),pose.Pos().Z()}}};
    std_msgs::msg::String message; message.data=value.dump(); event_->publish(message);
  }

  /** Encode a measured model or shaft pose and its measured physical velocity. */
  nlohmann::json Object(const std::string &id,const std::string &kind,bool shaft=false) {
    auto model=world_->ModelByName(id);
    auto pose=shaft ? model->GetLink("shaft")->WorldPose() : model->WorldPose();
    auto velocity=shaft ? model->GetLink("shaft")->WorldLinearVel() : model->WorldLinearVel();
    const auto &p=pose.Pos(); const auto &q=pose.Rot();
    return {{"id",id},{"kind",kind},{"position",{p.X(),p.Y(),p.Z()}},
      {"quaternion",{q.X(),q.Y(),q.Z(),q.W()}},{"velocity",{velocity.X(),velocity.Y(),velocity.Z()}}};
  }

  /** Publish measured snapshots at ten wall-clock hertz even when realtime factor is below one. */
  void PublishState() {
    auto objects=nlohmann::json::array(); auto joints=nlohmann::json::array();
    objects.push_back(Object("motor","motor",true));
    for (const auto &name:rollerNames_) {
      if (!world_->ModelByName(name)) continue;
      objects.push_back(Object(name,"roller",true));
      auto joint=world_->ModelByName(name)->GetJoint("shaft_joint");
      joints.push_back({{"id",name+"/shaft_joint"},{"position",joint->Position(0)},{"velocity",joint->GetVelocity(0)}});
    }
    for (const auto &product:products_) {
      if (!world_->ModelByName(product.slot)) continue;
      auto value=Object(product.slot,"product"); value["product_id"]=product.serial;
      value["stage"]=product.stage; value["inspection_id"]=product.inspection;
      value["classification"]=product.classification; value["outfeed_lane"]=product.outfeedLane;
      objects.push_back(value);
    }
    nlohmann::json inspection=nullptr;
    if (active_>=0) {
      const auto &product=products_[active_];
      inspection={{"product_id",product.serial},{"slot_id",product.slot},{"inspection_id",product.inspection},
          {"phase",product.readyTime<0 ? "settling" : "ready"},{"ready_sim_time",product.readyTime}};
    }
    const auto observedWallMs=std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count();
    nlohmann::json payload={{"run_id",run_},{"sequence",++sequence_},{"sim_time",world_->SimTime().Double()},
      {"observed_wall_time_ms",observedWallMs},
      {"world_version",version_},{"running",running_},{"inspection_hold",active_>=0},{"inspection",inspection},
      {"scenario_mode",defectMode_},
      {"transport_mode","physical_roller_friction"},{"lifecycle_reset_count",resets_},
      {"objects",objects},{"joints",joints}};
    std_msgs::msg::String message; message.data=payload.dump(); state_->publish(message);
    // The reference topic is internal ROS only. The bridge strips it from all public
    // world, camera, AI, lifecycle, and MQTT messages; it is written only to evaluation.
    auto references=nlohmann::json::array();
    for (const auto &product:products_)
      references.push_back({{"product_id",product.serial},{"slot_id",product.slot},{"class_id",product.defectClass}});
    nlohmann::json privatePayload={{"run_id",run_},{"sim_time",world_->SimTime().Double()},
      {"defect_mode",defectMode_},{"defect_probability",.02+.085*level_},{"products",references}};
    std_msgs::msg::String reference; reference.data=privatePayload.dump(); reference_->publish(reference);
  }

  physics::WorldPtr world_; gazebo_ros::Node::SharedPtr ros_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_,event_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr reference_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr command_;
  std::mutex mutex_; std::deque<std::string> pending_;
  std::vector<std::string> rollerNames_; std::vector<Product> products_;
  std::string run_,version_; bool running_=true; int active_=-1;
  std::string defectMode_; double level_=0,lastVisual_=-1; std::mt19937 rng_;
  double motorOmega_=100.0;
  bool diagnostics_=false,actuatorsConfigured_=false;
  transport::NodePtr transport_; transport::PublisherPtr visual_;
  unsigned long productCounter_=0,inspectionCounter_=0,sequence_=0,resets_=0;
  double lastState_=-1,lastJoint_=-1;
};
}
