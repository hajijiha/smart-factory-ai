/** Gazebo world plugin: ROS2 scene randomization, real motor joint observations and conveyor control. */
#include <gazebo/gazebo.hh>
#include <gazebo/physics/physics.hh>
#include <gazebo/transport/transport.hh>
#include <gazebo/msgs/msgs.hh>
#include <gazebo_ros/node.hpp>
#include <std_msgs/msg/string.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <nlohmann/json.hpp>
#include <mutex>
#include <deque>

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
  void Color(const std::string &name, const std::vector<double> &color) {
    msgs::Visual message; message.set_name(name); message.set_parent_name(name.substr(0, name.rfind("::")));
    auto *material = message.mutable_material();
    msgs::Set(material->mutable_ambient(), ignition::math::Color(color[0], color[1], color[2], 1));
    msgs::Set(material->mutable_diffuse(), ignition::math::Color(color[0], color[1], color[2], 1));
    visual_->Publish(message);
  }
  /** Apply scene commands on the physics thread and emit joint observations. */
  void Update() {
    auto motor = world_->ModelByName("motor");
    if (!motor) return;
    auto joint = motor->GetJoint("shaft_joint");
    joint->SetParam("fmax", 0, 100.0); joint->SetParam("vel", 0, 157.079632679);
    auto roller = world_->ModelByName("roller")->GetJoint("shaft_joint");
    roller->SetParam("fmax", 0, 100.0); roller->SetParam("vel", 0, running_ ? 4.0 : 0.0);
    std::deque<std::string> commands;
    { std::lock_guard<std::mutex> lock(mutex_); commands.swap(pending_); }
    for (const auto &command : commands) {
      try {
        auto j = nlohmann::json::parse(command);
        running_ = j.value("running", running_);
        if (j.contains("scene_id")) {
          double x=j["x"], y=j["y"], yaw=j["yaw"];
          ignition::math::Pose3d pose(x,y,.58,0,0,yaw);
          world_->ModelByName("product")->SetWorldPose(pose);
          for (int k=0; k<3; ++k) {
            auto model = world_->ModelByName("defect_"+std::to_string(k));
            if (j["class_id"].get<int>() == k) {
              auto defectPose = pose * ignition::math::Pose3d(j["dx"].get<double>(),j["dy"].get<double>(),.032,0,0,j["defect_yaw"].get<double>());
              model->SetWorldPose(defectPose);
            } else model->SetWorldPose(ignition::math::Pose3d(0,0,-10,0,0,0));
          }
          auto cameraPose = j["camera_pose"].get<std::vector<double>>();
          world_->ModelByName("camera")->SetWorldPose(ignition::math::Pose3d(cameraPose[0],cameraPose[1],cameraPose[2],cameraPose[3],cameraPose[4],cameraPose[5]));
          Color("product::body::visual", j["product_color"].get<std::vector<double>>());
          Color("conveyor::body::visual", j["background_color"].get<std::vector<double>>());
          msgs::Light light; light.set_name("sun");
          double value=j["light"];
          msgs::Set(light.mutable_diffuse(), ignition::math::Color(value,value,value,1));
          msgs::Set(light.mutable_specular(), ignition::math::Color(.1,.1,.1,1));
          light_->Publish(light);
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
};
GZ_REGISTER_WORLD_PLUGIN(FactoryWorld)
}
