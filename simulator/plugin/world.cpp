/** Gazebo world plugin: ROS2 scene randomization, real motor joint observations and conveyor control. */
#include <gazebo/gazebo.hh>
#include <gazebo/physics/physics.hh>
#include <gazebo/physics/ode/ODELink.hh>
#include <gazebo/transport/transport.hh>
#include <gazebo/msgs/msgs.hh>
#include <gazebo_ros/node.hpp>
#include <std_msgs/msg/string.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <nlohmann/json.hpp>
#include <mutex>
#include <deque>
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace gazebo {
class FactoryWorld : public WorldPlugin {
 public:
  /** Configure ROS2 subscriptions and physics update callbacks. */
  void Load(physics::WorldPtr world, sdf::ElementPtr sdf) override {
    world_ = world;
    ros_ = gazebo_ros::Node::Get(sdf);
    node_ = transport::NodePtr(new transport::Node()); node_->Init(world->Name());
    visual_ = node_->Advertise<msgs::Visual>("~/visual");
    light_ = node_->Advertise<msgs::Light>("~/light/modify");
    ready_ = ros_->create_publisher<std_msgs::msg::String>("/factory/scene_ready", 10);
    joints_ = ros_->create_publisher<sensor_msgs::msg::JointState>("/factory/motor_state", 10);
    command_ = ros_->create_subscription<std_msgs::msg::String>("/factory/scene", 10,
      [this](std_msgs::msg::String::SharedPtr message) {
        std::lock_guard<std::mutex> lock(mutex_); pending_.push_back(message->data);
      });
    update_ = event::Events::ConnectWorldUpdateBegin(std::bind(&FactoryWorld::Update, this));
  }
 private:
  /** Change a visual material through Gazebo transport, preserving genuine 3D rendering. */
  void Color(const std::string &name, const std::vector<double> &color, const std::vector<double> &specular = {}, bool flush = false) {
    msgs::Visual message; message.set_name(name); message.set_parent_name(name.substr(0, name.rfind("::")));
    auto *material = message.mutable_material();
    msgs::Set(material->mutable_ambient(), ignition::math::Color(color[0], color[1], color[2], 1));
    msgs::Set(material->mutable_diffuse(), ignition::math::Color(color[0], color[1], color[2], 1));
    if (specular.size() == 3) msgs::Set(material->mutable_specular(), ignition::math::Color(specular[0], specular[1], specular[2], 1));
    visual_->Publish(message, flush);
    if (flush) visual_->SendMessage();
  }
  /** Explicitly reissue static model visual poses independently of pose-topic timing. */
  void SetScenePose(physics::ModelPtr model, const ignition::math::Pose3d &pose, bool flush) {
    model->SetWorldPose(pose);
    if (!flush) return;
    msgs::Visual message;
    message.set_name(model->GetScopedName()); message.set_id(model->GetId());
    message.set_parent_name(world_->Name()); message.set_parent_id(0); message.set_type(msgs::Visual::MODEL);
    msgs::Set(message.mutable_pose(), model->RelativePose());
    visual_->Publish(message, true); visual_->SendMessage();
  }
  /** Update fixed 3D primitive slots using the same dimensions used by Python labels. */
  void DefectGeometry(int classId, const nlohmann::json &parts) {
    int slots = classId == 1 ? 2 : 5;
    for (int i = 0; i < slots; ++i) {
      const std::string name = "defect_" + std::to_string(classId) + "::body::part" + std::to_string(i);
      msgs::Visual message; message.set_name(name); message.set_parent_name("defect_" + std::to_string(classId) + "::body");
      if (i >= static_cast<int>(parts.size())) {
        msgs::Set(message.mutable_pose(), ignition::math::Pose3d(0,0,-10,0,0,0));
      } else {
        auto part = parts[i]; auto p = part["pose"].get<std::vector<double>>();
        msgs::Set(message.mutable_pose(), ignition::math::Pose3d(p[0],p[1],p[2],p[3],p[4],p[5]));
        if (part["shape"] == "box") {
          auto s = part["size"].get<std::vector<double>>();
          // Gazebo Visual::SetScale sets dimensions of the unit mesh directly;
          // it does not multiply the original SDF primitive dimensions.
          msgs::Set(message.mutable_scale(), ignition::math::Vector3d(s[0],s[1],s[2]));
        } else {
          double r = part["radius"], length = part["length"];
          msgs::Set(message.mutable_scale(), ignition::math::Vector3d(2*r,2*r,length));
        }
        auto color = part["color"].get<std::vector<double>>();
        auto *material = message.mutable_material();
        msgs::Set(material->mutable_ambient(), ignition::math::Color(color[0],color[1],color[2],1));
        msgs::Set(material->mutable_diffuse(), ignition::math::Color(color[0],color[1],color[2],1));
      }
      visual_->Publish(message, true);
      visual_->SendMessage();
    }
  }
  /** Apply scene commands on the physics thread and emit joint observations. */
  void Update() {
    auto motor = world_->ModelByName("motor");
    if (!motor) return;
    auto joint = motor->GetJoint("shaft_joint");
    joint->SetParam("fmax", 0, 100.0); joint->SetParam("vel", 0, motorOmega_);
    auto roller = world_->ModelByName("roller")->GetJoint("shaft_joint");
    roller->SetParam("fmax", 0, 100.0); roller->SetParam("vel", 0, running_ ? 4.0 : 0.0);
    std::deque<std::string> commands;
    { std::lock_guard<std::mutex> lock(mutex_); commands.swap(pending_); }
    for (const auto &command : commands) {
      try {
        auto j = nlohmann::json::parse(command);
        running_ = j.value("running", running_);
        if (j.contains("motor_rpm")) {
          double rpm = j["motor_rpm"];
          if (!std::isfinite(rpm) || rpm < 300 || rpm > 3000) throw std::runtime_error("Motor RPM outside supported range");
          motorOmega_ = rpm * 2.0 * std::acos(-1.0) / 60.0;
          if (j.value("generator_version", "v1") == "v2") {
            // Read-only physical diagnostics; acquisition still requires the
            // actual observed RPM, rather than replacing it with the command.
            auto shaft = boost::dynamic_pointer_cast<physics::ODELink>(motor->GetLink("shaft"));
            if (!shaft || !shaft->GetODEId()) throw std::runtime_error("v2 motor requires a physical ODE shaft body");
            double previousMax = dBodyGetMaxAngularSpeed(shaft->GetODEId());
            double previousJointLimit = joint->GetVelocityLimit(0);
            joint->SetParam("fmax", 0, 100.0);
            joint->SetParam("vel", 0, motorOmega_);
            auto diagnostic = nlohmann::json{
                {"commanded_rpm", rpm}, {"target_rad_s", motorOmega_},
                {"joint_vel_parameter", joint->GetParam("vel",0)},
                {"joint_fmax_parameter", joint->GetParam("fmax",0)},
                {"observed_rad_s", joint->GetVelocity(0)},
                {"joint_velocity_limit", previousJointLimit}};
            diagnostic["body_max_angular_speed"] = std::isfinite(previousMax) ? nlohmann::json(previousMax) : nlohmann::json("infinity");
            j["motor_diagnostics"] = diagnostic;
            gzmsg << "v2 motor command " << diagnostic.dump() << "\n";
          }
          j["sim_time"] = world_->SimTime().Double();
          std_msgs::msg::String ack; ack.data = j.dump(); ready_->publish(ack);
        }
        if (j.contains("scene_id")) {
          const bool v2 = j.value("generator_version", "v1") == "v2";
          if (v2 && (!j.contains("scene_command_id") || !j.contains("scene_application_id") ||
              !j.contains("scene_apply_round") || j["scene_apply_round"].get<int>() < 0 || j["scene_apply_round"].get<int>() > 2))
            throw std::runtime_error("v2 scene requires a unique application command and round 0..2");
          double x=j["x"], y=j["y"], yaw=j["yaw"];
          ignition::math::Pose3d pose(x,y,.58,0,0,yaw);
          SetScenePose(world_->ModelByName("product"),pose,v2);
          for (int k=0; k<3; ++k) {
            auto model = world_->ModelByName("defect_"+std::to_string(k));
            if (j["class_id"].get<int>() == k) {
              auto defectPose = pose * ignition::math::Pose3d(j["dx"].get<double>(),j["dy"].get<double>(),.032,0,0,j["defect_yaw"].get<double>());
              SetScenePose(model,defectPose,v2);
              if (v2) DefectGeometry(k,j["defect_primitives"]);
            } else SetScenePose(model,ignition::math::Pose3d(0,0,-10,0,0,0),v2);
          }
          auto cameraPose = j["camera_pose"].get<std::vector<double>>();
          SetScenePose(world_->ModelByName("camera"),ignition::math::Pose3d(cameraPose[0],cameraPose[1],cameraPose[2],cameraPose[3],cameraPose[4],cameraPose[5]),v2);
          Color("product::body::visual", j["product_color"].get<std::vector<double>>(),
              j.value("specular", std::vector<double>{}),v2);
          Color("conveyor::body::visual", j["background_color"].get<std::vector<double>>(),{},v2);
          msgs::Light light; light.set_name("sun");
          double value=j["light"];
          msgs::Set(light.mutable_diffuse(), ignition::math::Color(value,value,value,1));
          msgs::Set(light.mutable_specular(), ignition::math::Color(.1,.1,.1,1));
          light_->Publish(light,v2);
          if (v2) {
            light_->SendMessage();
            j["ack_scope"] = "physics_applied_and_visual_messages_enqueued";
            j["visual_outgoing_count"] = visual_->GetOutgoingCount();
          }
          j["sim_time"] = world_->SimTime().Double();
          std_msgs::msg::String ack; ack.data = j.dump(); ready_->publish(ack);
        }
      } catch (const std::exception &e) { gzerr << e.what() << "\n"; }
    }
    if ((world_->SimTime()-last_).Double() >= .02) {
      last_ = world_->SimTime();
      sensor_msgs::msg::JointState message;
      message.header.stamp.sec = world_->SimTime().sec;
      message.header.stamp.nanosec = world_->SimTime().nsec;
      message.name = {"motor_shaft", "conveyor_roller"};
      message.position = {joint->Position(0), roller->Position(0)};
      message.velocity = {joint->GetVelocity(0), roller->GetVelocity(0)};
      message.effort = {joint->GetForce(0), roller->GetForce(0)};
      joints_->publish(message);
    }
  }
  physics::WorldPtr world_; gazebo_ros::Node::SharedPtr ros_;
  transport::NodePtr node_; transport::PublisherPtr visual_, light_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr ready_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr joints_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr command_;
  event::ConnectionPtr update_; common::Time last_; std::mutex mutex_;
  std::deque<std::string> pending_; bool running_ = true;
  double motorOmega_ = 157.079632679;
};
GZ_REGISTER_WORLD_PLUGIN(FactoryWorld)
}
