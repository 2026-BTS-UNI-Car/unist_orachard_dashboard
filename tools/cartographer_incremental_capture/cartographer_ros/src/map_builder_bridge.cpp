/*
 * Copyright 2016 The Cartographer Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "cartographer_ros/map_builder_bridge.h"
#include <zlib.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <limits>
#include <map>
#include <mutex>
#include <random>
#include <sstream>
#include <stdexcept>
#include <time.h>
#include <vector>
#include "cartographer/mapping/2d/grid_2d.h"
#include "cartographer/mapping/2d/probability_grid.h"
#include "cartographer/mapping/2d/submap_2d.h"
#include "std_msgs/msg/string.hpp"
#include "cartographer_ros/dense_export.h"

#include "absl/memory/memory.h"
#include "cartographer/io/color.h"
#include "cartographer/io/proto_stream.h"
#include "cartographer_ros/msg_conversion.h"
#include "cartographer_ros/time_conversion.h"
#include "cartographer_ros_msgs/msg/status_code.hpp"
#include "cartographer_ros_msgs/msg/status_response.hpp"

namespace cartographer_ros {
namespace {

using ::cartographer::transform::Rigid3d;

constexpr double kTrajectoryLineStripMarkerScale = 0.07;
constexpr double kLandmarkMarkerScale = 0.2;
constexpr double kConstraintMarkerScale = 0.025;

::std_msgs::msg::ColorRGBA ToMessage(const cartographer::io::FloatColor& color) {
  ::std_msgs::msg::ColorRGBA result;
  result.r = color[0];
  result.g = color[1];
  result.b = color[2];
  result.a = 1.f;
  return result;
}

visualization_msgs::msg::Marker CreateTrajectoryMarker(const int trajectory_id,
                                                  const std::string& frame_id,
                                                  rclcpp::Time node_time) {
  visualization_msgs::msg::Marker marker;
  marker.ns = "Trajectory " + std::to_string(trajectory_id);
  marker.id = 0;
  marker.type = visualization_msgs::msg::Marker::LINE_STRIP;
  marker.header.stamp = node_time;
  marker.header.frame_id = frame_id;
  marker.color = ToMessage(cartographer::io::GetColor(trajectory_id));
  marker.scale.x = kTrajectoryLineStripMarkerScale;
  marker.pose.orientation.w = 1.;
  marker.pose.position.z = 0.05;
  return marker;
}

int GetLandmarkIndex(
    const std::string& landmark_id,
    std::unordered_map<std::string, int>* landmark_id_to_index) {
  auto it = landmark_id_to_index->find(landmark_id);
  if (it == landmark_id_to_index->end()) {
    const int new_index = landmark_id_to_index->size();
    landmark_id_to_index->emplace(landmark_id, new_index);
    return new_index;
  }
  return it->second;
}

visualization_msgs::msg::Marker CreateLandmarkMarker(int landmark_index,
                                                const Rigid3d& landmark_pose,
                                                const std::string& frame_id,
                                                rclcpp::Time node_time) {
  visualization_msgs::msg::Marker marker;
  marker.ns = "Landmarks";
  marker.id = landmark_index;
  marker.type = visualization_msgs::msg::Marker::SPHERE;
  marker.header.stamp = node_time;
  marker.header.frame_id = frame_id;
  marker.scale.x = kLandmarkMarkerScale;
  marker.scale.y = kLandmarkMarkerScale;
  marker.scale.z = kLandmarkMarkerScale;
  marker.color = ToMessage(cartographer::io::GetColor(landmark_index));
  marker.pose = ToGeometryMsgPose(landmark_pose);
  return marker;
}

void PushAndResetLineMarker(visualization_msgs::msg::Marker* marker,
                            std::vector<visualization_msgs::msg::Marker>* markers) {
  markers->push_back(*marker);
  ++marker->id;
  marker->points.clear();
}

}  // namespace

MapBuilderBridge::MapBuilderBridge(
    const NodeOptions& node_options,
    std::unique_ptr<cartographer::mapping::MapBuilderInterface> map_builder,
    tf2_ros::Buffer* const tf_buffer)
    : node_options_(node_options),
      map_builder_(std::move(map_builder)),
      tf_buffer_(tf_buffer) {}

void MapBuilderBridge::LoadState(const std::string& state_filename,
                                 bool load_frozen_state) {
  // Check if suffix of the state file is ".pbstream".
  const std::string suffix = ".pbstream";
  CHECK_EQ(state_filename.substr(
               std::max<int>(state_filename.size() - suffix.size(), 0)),
           suffix)
      << "The file containing the state to be loaded must be a "
         ".pbstream file.";
  LOG(INFO) << "Loading saved state '" << state_filename << "'...";
  cartographer::io::ProtoStreamReader stream(state_filename);
  map_builder_->LoadState(&stream, load_frozen_state);
}

int MapBuilderBridge::AddTrajectory(
    const std::set<cartographer::mapping::TrajectoryBuilderInterface::SensorId>&
        expected_sensor_ids,
    const TrajectoryOptions& trajectory_options) {
  const int trajectory_id = map_builder_->AddTrajectoryBuilder(
      expected_sensor_ids, trajectory_options.trajectory_builder_options,
      [this](const int trajectory_id, const ::cartographer::common::Time time,
             const Rigid3d local_pose,
             ::cartographer::sensor::RangeData range_data_in_local,
             const std::unique_ptr<
                 const ::cartographer::mapping::TrajectoryBuilderInterface::
                     InsertionResult> insertion_result) {
        OnLocalSlamResult(trajectory_id, time, local_pose, range_data_in_local,
                          insertion_result != nullptr);
      });
  LOG(INFO) << "Added trajectory with ID '" << trajectory_id << "'.";

  // Make sure there is no trajectory with 'trajectory_id' yet.
  CHECK_EQ(sensor_bridges_.count(trajectory_id), 0);
  sensor_bridges_[trajectory_id] = absl::make_unique<SensorBridge>(
      trajectory_options.num_subdivisions_per_laser_scan,
      trajectory_options.tracking_frame,
      node_options_.lookup_transform_timeout_sec, tf_buffer_,
      map_builder_->GetTrajectoryBuilder(trajectory_id));
  auto emplace_result =
      trajectory_options_.emplace(trajectory_id, trajectory_options);
  CHECK(emplace_result.second == true);
  return trajectory_id;
}

void MapBuilderBridge::FinishTrajectory(const int trajectory_id) {
  LOG(INFO) << "Finishing trajectory with ID '" << trajectory_id << "'...";

  // Make sure there is a trajectory with 'trajectory_id'.
  CHECK(GetTrajectoryStates().count(trajectory_id));
  map_builder_->FinishTrajectory(trajectory_id);
  sensor_bridges_.erase(trajectory_id);
}

void MapBuilderBridge::RunFinalOptimization() {
  LOG(INFO) << "Running final trajectory optimization...";
  map_builder_->pose_graph()->RunFinalOptimization();
}

bool MapBuilderBridge::SerializeState(const std::string& filename,
                                      const bool include_unfinished_submaps) {
  return map_builder_->SerializeStateToFile(include_unfinished_submaps,
                                            filename);
}

void MapBuilderBridge::HandleSubmapQuery(
    const cartographer_ros_msgs::srv::SubmapQuery::Request::SharedPtr request,
    cartographer_ros_msgs::srv::SubmapQuery::Response::SharedPtr response) {
  cartographer::mapping::proto::SubmapQuery::Response response_proto;
  cartographer::mapping::SubmapId submap_id{request->trajectory_id,
                                            request->submap_index};
  const std::string error =
      map_builder_->SubmapToProto(submap_id, &response_proto);
  if (!error.empty()) {
    LOG(ERROR) << error;
    response->status.code = cartographer_ros_msgs::msg::StatusCode::NOT_FOUND;
    response->status.message = error;
    return;
  }

  response->submap_version = response_proto.submap_version();
  for (const auto& texture_proto : response_proto.textures()) {
    response->textures.emplace_back();
    auto& texture = response->textures.back();
    texture.cells.insert(texture.cells.begin(), texture_proto.cells().begin(),
                         texture_proto.cells().end());
    texture.width = texture_proto.width();
    texture.height = texture_proto.height();
    texture.resolution = texture_proto.resolution();
    texture.slice_pose = ToGeometryMsgPose(
        cartographer::transform::ToRigid3(texture_proto.slice_pose()));
  }
  response->status.message = "Success.";
  response->status.code = cartographer_ros_msgs::msg::StatusCode::OK;
}

std::map<int, ::cartographer::mapping::PoseGraphInterface::TrajectoryState>
MapBuilderBridge::GetTrajectoryStates() {
  auto trajectory_states = map_builder_->pose_graph()->GetTrajectoryStates();
  // Add active trajectories that are not yet in the pose graph, but are e.g.
  // waiting for input sensor data and thus already have a sensor bridge.
  for (const auto& sensor_bridge : sensor_bridges_) {
    trajectory_states.insert(std::make_pair(
        sensor_bridge.first,
        ::cartographer::mapping::PoseGraphInterface::TrajectoryState::ACTIVE));
  }
  return trajectory_states;
}

cartographer_ros_msgs::msg::SubmapList MapBuilderBridge::GetSubmapList(rclcpp::Time node_time) {
  cartographer_ros_msgs::msg::SubmapList submap_list;
  submap_list.header.stamp = node_time;
  submap_list.header.frame_id = node_options_.map_frame;
  for (const auto& submap_id_pose :
       map_builder_->pose_graph()->GetAllSubmapPoses()) {
    cartographer_ros_msgs::msg::SubmapEntry submap_entry;
    submap_entry.is_frozen = map_builder_->pose_graph()->IsTrajectoryFrozen(
        submap_id_pose.id.trajectory_id);
    submap_entry.trajectory_id = submap_id_pose.id.trajectory_id;
    submap_entry.submap_index = submap_id_pose.id.submap_index;
    submap_entry.submap_version = submap_id_pose.data.version;
    submap_entry.pose = ToGeometryMsgPose(submap_id_pose.data.pose);
    submap_list.submap.push_back(submap_entry);
  }
  return submap_list;
}

std::unordered_map<int, MapBuilderBridge::LocalTrajectoryData>
MapBuilderBridge::GetLocalTrajectoryData() {
  std::unordered_map<int, LocalTrajectoryData> local_trajectory_data;
  for (const auto& entry : sensor_bridges_) {
    const int trajectory_id = entry.first;
    const SensorBridge& sensor_bridge = *entry.second;

    std::shared_ptr<const LocalTrajectoryData::LocalSlamData> local_slam_data;
    {
      absl::MutexLock lock(&mutex_);
      if (local_slam_data_.count(trajectory_id) == 0) {
        continue;
      }
      local_slam_data = local_slam_data_.at(trajectory_id);
    }

    // Make sure there is a trajectory with 'trajectory_id'.
    CHECK_EQ(trajectory_options_.count(trajectory_id), 1);
    local_trajectory_data[trajectory_id] = {
        local_slam_data,
        map_builder_->pose_graph()->GetLocalToGlobalTransform(trajectory_id),
        sensor_bridge.tf_bridge().LookupToTracking(
            local_slam_data->time,
            trajectory_options_[trajectory_id].published_frame),
        trajectory_options_[trajectory_id]};
  }
  return local_trajectory_data;
}

void MapBuilderBridge::HandleTrajectoryQuery(
    const cartographer_ros_msgs::srv::TrajectoryQuery::Request::SharedPtr request,
    cartographer_ros_msgs::srv::TrajectoryQuery::Response::SharedPtr response) {
  // This query is safe if the trajectory doesn't exist (returns 0 poses).
  // However, we can filter unwanted states at the higher level in the node.
  const auto node_poses = map_builder_->pose_graph()->GetTrajectoryNodePoses();
  for (const auto& node_id_data :
       node_poses.trajectory(request->trajectory_id)) {
    if (!node_id_data.data.constant_pose_data.has_value()) {
      continue;
    }
    geometry_msgs::msg::PoseStamped pose_stamped;
    pose_stamped.header.frame_id = node_options_.map_frame;
    pose_stamped.header.stamp =
        ToRos(node_id_data.data.constant_pose_data.value().time);
    pose_stamped.pose = ToGeometryMsgPose(node_id_data.data.global_pose);
    response->trajectory.push_back(pose_stamped);
  }
  response->status.code = cartographer_ros_msgs::msg::StatusCode::OK;
  response->status.message =
      "Retrieved " + std::to_string(response->trajectory.size()) +
      " trajectory nodes from trajectory " + std::to_string(request->trajectory_id) + ".";
}

visualization_msgs::msg::MarkerArray MapBuilderBridge::GetTrajectoryNodeList(
    rclcpp::Time node_time)
{
  visualization_msgs::msg::MarkerArray trajectory_node_list;
  const auto node_poses = map_builder_->pose_graph()->GetTrajectoryNodePoses();
  // Find the last node indices for each trajectory that have either
  // inter-submap or inter-trajectory constraints.
  std::map<int, int /* node_index */>
      trajectory_to_last_inter_submap_constrained_node;
  std::map<int, int /* node_index */>
      trajectory_to_last_inter_trajectory_constrained_node;
  for (const int trajectory_id : node_poses.trajectory_ids()) {
    trajectory_to_last_inter_submap_constrained_node[trajectory_id] = 0;
    trajectory_to_last_inter_trajectory_constrained_node[trajectory_id] = 0;
  }
  const auto constraints = map_builder_->pose_graph()->constraints();
  for (const auto& constraint : constraints) {
    if (constraint.tag ==
        cartographer::mapping::PoseGraphInterface::Constraint::INTER_SUBMAP) {
      if (constraint.node_id.trajectory_id ==
          constraint.submap_id.trajectory_id) {
        trajectory_to_last_inter_submap_constrained_node[constraint.node_id
                                                             .trajectory_id] =
            std::max(trajectory_to_last_inter_submap_constrained_node.at(
                         constraint.node_id.trajectory_id),
                     constraint.node_id.node_index);
      } else {
        trajectory_to_last_inter_trajectory_constrained_node
            [constraint.node_id.trajectory_id] =
                std::max(trajectory_to_last_inter_submap_constrained_node.at(
                             constraint.node_id.trajectory_id),
                         constraint.node_id.node_index);
      }
    }
  }

  for (const int trajectory_id : node_poses.trajectory_ids()) {
    visualization_msgs::msg::Marker marker =
        CreateTrajectoryMarker(trajectory_id, node_options_.map_frame, node_time);
    int last_inter_submap_constrained_node = std::max(
        node_poses.trajectory(trajectory_id).begin()->id.node_index,
        trajectory_to_last_inter_submap_constrained_node.at(trajectory_id));
    int last_inter_trajectory_constrained_node = std::max(
        node_poses.trajectory(trajectory_id).begin()->id.node_index,
        trajectory_to_last_inter_trajectory_constrained_node.at(trajectory_id));
    last_inter_submap_constrained_node =
        std::max(last_inter_submap_constrained_node,
                 last_inter_trajectory_constrained_node);

    if (map_builder_->pose_graph()->IsTrajectoryFrozen(trajectory_id)) {
      last_inter_submap_constrained_node =
          (--node_poses.trajectory(trajectory_id).end())->id.node_index;
      last_inter_trajectory_constrained_node =
          last_inter_submap_constrained_node;
    }

    marker.color.a = 1.0;
    for (const auto& node_id_data : node_poses.trajectory(trajectory_id)) {
      if (!node_id_data.data.constant_pose_data.has_value()) {
        PushAndResetLineMarker(&marker, &trajectory_node_list.markers);
        continue;
      }
      const ::geometry_msgs::msg::Point node_point =
          ToGeometryMsgPoint(node_id_data.data.global_pose.translation());
      marker.points.push_back(node_point);

      if (node_id_data.id.node_index ==
          last_inter_trajectory_constrained_node) {
        PushAndResetLineMarker(&marker, &trajectory_node_list.markers);
        marker.points.push_back(node_point);
        marker.color.a = 0.5;
      }
      if (node_id_data.id.node_index == last_inter_submap_constrained_node) {
        PushAndResetLineMarker(&marker, &trajectory_node_list.markers);
        marker.points.push_back(node_point);
        marker.color.a = 0.25;
      }
      // Work around the 16384 point limit in RViz by splitting the
      // trajectory into multiple markers.
      if (marker.points.size() == 16384) {
        PushAndResetLineMarker(&marker, &trajectory_node_list.markers);
        // Push back the last point, so the two markers appear connected.
        marker.points.push_back(node_point);
      }
    }
    PushAndResetLineMarker(&marker, &trajectory_node_list.markers);
    size_t current_last_marker_id = static_cast<size_t>(marker.id - 1);
    if (trajectory_to_highest_marker_id_.count(trajectory_id) == 0) {
      trajectory_to_highest_marker_id_[trajectory_id] = current_last_marker_id;
    } else {
      marker.action = visualization_msgs::msg::Marker::DELETE;
      while (static_cast<size_t>(marker.id) <=
             trajectory_to_highest_marker_id_[trajectory_id]) {
        trajectory_node_list.markers.push_back(marker);
        ++marker.id;
      }
      trajectory_to_highest_marker_id_[trajectory_id] = current_last_marker_id;
    }
  }
  return trajectory_node_list;
}

visualization_msgs::msg::MarkerArray MapBuilderBridge::GetLandmarkPosesList(
    rclcpp::Time node_time)
{
  visualization_msgs::msg::MarkerArray landmark_poses_list;
  const std::map<std::string, Rigid3d> landmark_poses =
      map_builder_->pose_graph()->GetLandmarkPoses();
  for (const auto& id_to_pose : landmark_poses) {
    landmark_poses_list.markers.push_back(CreateLandmarkMarker(
        GetLandmarkIndex(id_to_pose.first, &landmark_to_index_),
        id_to_pose.second, node_options_.map_frame, node_time));
  }
  return landmark_poses_list;
}

visualization_msgs::msg::MarkerArray MapBuilderBridge::GetConstraintList(rclcpp::Time node_time) {
  visualization_msgs::msg::MarkerArray constraint_list;
  int marker_id = 0;
  visualization_msgs::msg::Marker constraint_intra_marker;
  constraint_intra_marker.id = marker_id++;
  constraint_intra_marker.ns = "Intra constraints";
  constraint_intra_marker.type = visualization_msgs::msg::Marker::LINE_LIST;
  constraint_intra_marker.header.stamp = node_time;
  constraint_intra_marker.header.frame_id = node_options_.map_frame;
  constraint_intra_marker.scale.x = kConstraintMarkerScale;
  constraint_intra_marker.pose.orientation.w = 1.0;

  visualization_msgs::msg::Marker residual_intra_marker = constraint_intra_marker;
  residual_intra_marker.id = marker_id++;
  residual_intra_marker.ns = "Intra residuals";
  // This and other markers which are less numerous are set to be slightly
  // above the intra constraints marker in order to ensure that they are
  // visible.
  residual_intra_marker.pose.position.z = 0.1;

  visualization_msgs::msg::Marker constraint_inter_same_trajectory_marker =
      constraint_intra_marker;
  constraint_inter_same_trajectory_marker.id = marker_id++;
  constraint_inter_same_trajectory_marker.ns =
      "Inter constraints, same trajectory";
  constraint_inter_same_trajectory_marker.pose.position.z = 0.1;

  visualization_msgs::msg::Marker residual_inter_same_trajectory_marker =
      constraint_intra_marker;
  residual_inter_same_trajectory_marker.id = marker_id++;
  residual_inter_same_trajectory_marker.ns = "Inter residuals, same trajectory";
  residual_inter_same_trajectory_marker.pose.position.z = 0.1;

  visualization_msgs::msg::Marker constraint_inter_diff_trajectory_marker =
      constraint_intra_marker;
  constraint_inter_diff_trajectory_marker.id = marker_id++;
  constraint_inter_diff_trajectory_marker.ns =
      "Inter constraints, different trajectories";
  constraint_inter_diff_trajectory_marker.pose.position.z = 0.1;

  visualization_msgs::msg::Marker residual_inter_diff_trajectory_marker =
      constraint_intra_marker;
  residual_inter_diff_trajectory_marker.id = marker_id++;
  residual_inter_diff_trajectory_marker.ns =
      "Inter residuals, different trajectories";
  residual_inter_diff_trajectory_marker.pose.position.z = 0.1;

  const auto trajectory_node_poses =
      map_builder_->pose_graph()->GetTrajectoryNodePoses();
  const auto submap_poses = map_builder_->pose_graph()->GetAllSubmapPoses();
  const auto constraints = map_builder_->pose_graph()->constraints();

  for (const auto& constraint : constraints) {
    visualization_msgs::msg::Marker *constraint_marker, *residual_marker;
    std_msgs::msg::ColorRGBA color_constraint, color_residual;
    if (constraint.tag ==
        cartographer::mapping::PoseGraphInterface::Constraint::INTRA_SUBMAP) {
      constraint_marker = &constraint_intra_marker;
      residual_marker = &residual_intra_marker;
      // Color mapping for submaps of various trajectories - add trajectory id
      // to ensure different starting colors. Also add a fixed offset of 25
      // to avoid having identical colors as trajectories.
      color_constraint = ToMessage(
          cartographer::io::GetColor(constraint.submap_id.submap_index +
                                     constraint.submap_id.trajectory_id + 25));
      color_residual.a = 1.0;
      color_residual.r = 1.0;
    } else {
      if (constraint.node_id.trajectory_id ==
          constraint.submap_id.trajectory_id) {
        constraint_marker = &constraint_inter_same_trajectory_marker;
        residual_marker = &residual_inter_same_trajectory_marker;
        // Bright yellow
        color_constraint.a = 1.0;
        color_constraint.r = color_constraint.g = 1.0;
      } else {
        constraint_marker = &constraint_inter_diff_trajectory_marker;
        residual_marker = &residual_inter_diff_trajectory_marker;
        // Bright orange
        color_constraint.a = 1.0;
        color_constraint.r = 1.0;
        color_constraint.g = 165. / 255.;
      }
      // Bright cyan
      color_residual.a = 1.0;
      color_residual.b = color_residual.g = 1.0;
    }

    for (int i = 0; i < 2; ++i) {
      constraint_marker->colors.push_back(color_constraint);
      residual_marker->colors.push_back(color_residual);
    }

    const auto submap_it = submap_poses.find(constraint.submap_id);
    if (submap_it == submap_poses.end()) {
      continue;
    }
    const auto& submap_pose = submap_it->data.pose;
    const auto node_it = trajectory_node_poses.find(constraint.node_id);
    if (node_it == trajectory_node_poses.end()) {
      continue;
    }
    const auto& trajectory_node_pose = node_it->data.global_pose;
    const Rigid3d constraint_pose = submap_pose * constraint.pose.zbar_ij;

    constraint_marker->points.push_back(
        ToGeometryMsgPoint(submap_pose.translation()));
    constraint_marker->points.push_back(
        ToGeometryMsgPoint(constraint_pose.translation()));

    residual_marker->points.push_back(
        ToGeometryMsgPoint(constraint_pose.translation()));
    residual_marker->points.push_back(
        ToGeometryMsgPoint(trajectory_node_pose.translation()));
  }

  constraint_list.markers.push_back(constraint_intra_marker);
  constraint_list.markers.push_back(residual_intra_marker);
  constraint_list.markers.push_back(constraint_inter_same_trajectory_marker);
  constraint_list.markers.push_back(residual_inter_same_trajectory_marker);
  constraint_list.markers.push_back(constraint_inter_diff_trajectory_marker);
  constraint_list.markers.push_back(residual_inter_diff_trajectory_marker);
  return constraint_list;
}

SensorBridge* MapBuilderBridge::sensor_bridge(const int trajectory_id) {
  return sensor_bridges_.at(trajectory_id).get();
}

void MapBuilderBridge::OnLocalSlamResult(
    const int trajectory_id, const ::cartographer::common::Time time,
    const Rigid3d local_pose,
    ::cartographer::sensor::RangeData range_data_in_local, const bool inserted) {
  ubts_offline_diagnostic::Writer().Add(trajectory_id,
      ::cartographer::common::ToUniversal(time),
      local_pose.translation().x(), local_pose.translation().y(), local_pose.translation().z(),
      local_pose.rotation().x(), local_pose.rotation().y(), local_pose.rotation().z(),
      local_pose.rotation().w(), inserted);
  std::shared_ptr<const LocalTrajectoryData::LocalSlamData> local_slam_data =
      std::make_shared<LocalTrajectoryData::LocalSlamData>(
          LocalTrajectoryData::LocalSlamData{time, local_pose,
                                             std::move(range_data_in_local)});
  absl::MutexLock lock(&mutex_);
  local_slam_data_[trajectory_id] = std::move(local_slam_data);
}

// Optional observation only. Caller holds Node's ingress mutex while capturing.
namespace {
using NativeKey = std::pair<int, int>;
std::string NativeJson(const std::string& value) {
  std::ostringstream out; out << '"';
  for (unsigned char c : value) {
    if (c == '"' || c == '\\') out << '\\' << char(c);
    else if (c < 32) out << "\\u00" << std::hex << std::setw(2)
                        << std::setfill('0') << unsigned(c) << std::dec;
    else out << char(c);
  }
  out << '"'; return out.str();
}
std::int64_t NativeRawNow() {
  timespec ts{};
  if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) return 0;
  return std::int64_t(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}
std::int64_t NativeThreadCpuNow() {
  timespec ts{};
  if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts) != 0) return 0;
  return std::int64_t(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}
std::string NativeEpoch() {
  std::random_device random; std::ostringstream out;
  for (int i = 0; i < 4; ++i)
    out << std::hex << std::setw(8) << std::setfill('0') << std::uint32_t(random());
  return out.str();
}
std::string NativeBase64(const char* data, std::size_t size) {
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string result; result.reserve(((size + 2) / 3) * 4);
  for (std::size_t i = 0; i < size; i += 3) {
    const unsigned a = static_cast<unsigned char>(data[i]);
    const unsigned b = i + 1 < size ? static_cast<unsigned char>(data[i + 1]) : 0;
    const unsigned c = i + 2 < size ? static_cast<unsigned char>(data[i + 2]) : 0;
    result.push_back(alphabet[a >> 2]); result.push_back(alphabet[((a & 3) << 4) | (b >> 4)]);
    result.push_back(i + 1 < size ? alphabet[((b & 15) << 2) | (c >> 6)] : '=');
    result.push_back(i + 2 < size ? alphabet[c & 63] : '=');
  }
  return result;
}
// Lossless RFC1950 transport only: the native float/mask bytes are unchanged.
std::string NativeCompressedChunk(const char* probabilities, const char* known,
                                  std::size_t count) {
  if (count < 1 || count > 16384) throw std::length_error("native_compression_count_cap");
  std::string raw(probabilities, 4 * count); raw.append(known, count);
  uLongf size = compressBound(static_cast<uLong>(raw.size()));
  if (size > 81958) throw std::length_error("native_compression_buffer_cap");
  std::string compressed(size, '\0');
  const int result = compress2(reinterpret_cast<Bytef*>(&compressed[0]), &size,
      reinterpret_cast<const Bytef*>(raw.data()), static_cast<uLong>(raw.size()), Z_BEST_SPEED);
  if (result != Z_OK) throw std::runtime_error("native_compression_failed");
  compressed.resize(size);
  return NativeBase64(compressed.data(), compressed.size());
}
void NativeFloat(float value, std::string* out) {
  static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
                "IEEE754 float32 required");
  std::uint32_t bits; std::memcpy(&bits, &value, sizeof(bits));
  for (int i = 0; i < 4; ++i) out->push_back(char((bits >> (8 * i)) & 255));
}
std::string NativeFingerprint(const std::string& probabilities, const std::string& known) {
  // Noncryptographic framing diagnostic, explicitly not a SHA or attestation.
  std::uint64_t value = 14695981039346656037ULL;
  for (const auto* part : {&probabilities, &known})
    for (unsigned char c : *part) { value ^= c; value *= 1099511628211ULL; }
  std::ostringstream out; out << std::hex << std::setw(16) << std::setfill('0') << value;
  return out.str();
}
std::string NativePose(const cartographer::transform::Rigid3d& pose) {
  const auto& t = pose.translation(); const auto& q = pose.rotation();
  if (!t.allFinite() || !q.coeffs().allFinite()) throw std::runtime_error("nonfinite_native_pose");
  std::ostringstream out; out << std::setprecision(17)
    << "{\"translation\":[" << t.x() << ',' << t.y() << ',' << t.z()
    << "],\"q_xyzw\":[" << q.x() << ',' << q.y() << ',' << q.z() << ',' << q.w() << "]}";
  return out.str();
}
std::int64_t NativeParameter(const rclcpp::Node::SharedPtr& node,
    const std::string& name, std::int64_t value, std::int64_t maximum) {
  if (!node->has_parameter(name)) node->declare_parameter<std::int64_t>(name, value);
  const auto result = node->get_parameter(name).as_int();
  if (result < 1 || result > maximum) throw std::runtime_error("native_probability_parameter_cap");
  return result;
}
struct NativeBody {
  static_assert(sizeof(std::size_t) >= 8, "64-bit size_t required");
  NativeKey key; const cartographer::mapping::Submap* source = nullptr;
  const cartographer::mapping::Grid2D* grid_source = nullptr;
  int version = 0; bool finished = false;
  int i0 = 0, i1 = 0, n0 = 0, n1 = 0;
  double resolution = 0, max_x = 0, max_y = 0;
  std::uint64_t body_id = 0, known_count = 0;
  std::string local_pose, probabilities, known, fingerprint;
  std::size_t cells() const { return std::size_t(n0) * std::size_t(n1); }
};
struct NativeEntry { std::shared_ptr<const NativeBody> body; std::string global_pose; };
struct NativeSnapshot {
  std::uint64_t sequence = 0, transform_revision = 0, generation = 0;
  std::int64_t ros_ns = 0, raw_ns = 0;
  std::int64_t capture_wall_ns = 0, capture_thread_cpu_ns = 0;
  std::uint64_t copied_bytes = 0;
  std::vector<NativeEntry> entries;
  std::string source_times;
};
}  // namespace

struct NativeGridPublisherState {
  std::mutex mutex;
  std::mutex pump_mutex;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher;
  rclcpp::Clock::SharedPtr clock;
  std::string epoch = NativeEpoch(), frame, error, pose_signature;
  bool cancelled = false, limited = false;
  bool compress_chunks = false;
  std::size_t max_submaps = 64, max_update_cells = 1048576, max_cached_cells = 4194304;
  std::size_t chunk_cells = 4096, packets_per_tick = 32, full_refresh_batches = 20;
  std::int64_t min_capture_ns = 500000000, last_capture_raw_ns = 0;
  std::uint64_t generation = 1, next_body_id = 1, captures = 0, copies = 0, copied_cells = 0;
  std::int64_t last_capture_call_wall_ns = 0, last_capture_call_thread_cpu_ns = 0;
  std::uint64_t superseded = 0, errors = 0, packets = 0, completed = 0, transform_revision = 0;
  std::map<NativeKey, std::shared_ptr<const NativeBody>> cache;
  std::shared_ptr<const NativeSnapshot> pending, inflight;
  std::map<NativeKey, std::uint64_t> last_completed_bodies;
  bool manifest_pending = false, full_refresh = false, status_pending = false;
  std::vector<std::size_t> send_entries;
  std::size_t send_index = 0, send_offset = 0;
};

void MapBuilderBridge::ConfigureNativeProbabilityPublisher(const rclcpp::Node::SharedPtr& node) {
  std::lock_guard<std::mutex> setup(derived_setup_mutex_);
  if(derived_setup_sealed_)throw std::runtime_error("publisher_setup_sealed_before_callbacks");
  if(derived_display_capture_)throw std::runtime_error("legacy_derived_modes_mutually_exclusive");
  if (native_grid_publisher_) throw std::runtime_error("native_publisher_already_configured");
  auto state = std::make_shared<NativeGridPublisherState>();
  state->frame = node_options_.map_frame;
  state->clock = node->get_clock();
  state->max_submaps = NativeParameter(node, "native_probability_max_submaps", 64, 256);
  state->max_update_cells = NativeParameter(node, "native_probability_max_update_cells", 1048576, 4194304);
  state->max_cached_cells = NativeParameter(node, "native_probability_max_cached_cells", 4194304, 16777216);
  state->chunk_cells = NativeParameter(node, "native_probability_chunk_cells", 4096, 16384);
  state->packets_per_tick = NativeParameter(node, "native_probability_packets_per_tick", 32, 128);
  state->full_refresh_batches = NativeParameter(node, "native_probability_full_refresh_batches", 20, 1024);
  const auto min_capture_ms = NativeParameter(node, "native_probability_min_capture_period_ms", 500, 60000);
  if (min_capture_ms < 500) throw std::runtime_error("native_capture_period_minimum_500ms");
  state->min_capture_ns = 1000000 * min_capture_ms;
  if (!node->has_parameter("native_probability_reliable"))
    node->declare_parameter<bool>("native_probability_reliable", false);
  const bool reliable = node->get_parameter("native_probability_reliable").as_bool();
  if (!node->has_parameter("native_probability_compress_chunks"))
    node->declare_parameter<bool>("native_probability_compress_chunks", false);
  state->compress_chunks = node->get_parameter("native_probability_compress_chunks").as_bool();
  state->publisher = node->create_publisher<std_msgs::msg::String>(
      "~/native_probability_submaps", reliable
          ? rclcpp::QoS(128).reliable().durability_volatile()
          : rclcpp::QoS(16).best_effort().durability_volatile());
  native_grid_publisher_ = std::move(state);
}

bool MapBuilderBridge::NativeProbabilityPublisherEnabled() const {
  return bool(native_grid_publisher_);
}

void MapBuilderBridge::CaptureNativeProbabilitySubmaps(std::int64_t captured_ros_ns) {
  const auto state = native_grid_publisher_;
  if (!state) return;
  const auto capture_started = NativeRawNow();
  const auto cpu_started = NativeThreadCpuNow();
  // Constructed before cache temporaries: destructor includes their release.
  // Measures capture-call time under Node mutex, not lock wait or normal list publication.
  struct CaptureTimer {
    std::shared_ptr<NativeGridPublisherState> state;
    std::int64_t wall, cpu;
    bool active = false;
    ~CaptureTimer() {
      if (!active) return;
      const auto end_wall = NativeRawNow(), end_cpu = NativeThreadCpuNow();
      std::lock_guard<std::mutex> lock(state->mutex);
      state->last_capture_call_wall_ns = end_wall > wall ? end_wall - wall : 0;
      state->last_capture_call_thread_cpu_ns = end_cpu > cpu ? end_cpu - cpu : 0;
    }
  } timer{state, capture_started, cpu_started};
  std::map<NativeKey, std::shared_ptr<const NativeBody>> previous;
  std::uint64_t generation, next_body;
  {
    std::lock_guard<std::mutex> lock(state->mutex);
    if (state->cancelled || state->limited) return;
    if (capture_started > 0 && state->last_capture_raw_ns > 0 &&
        capture_started - state->last_capture_raw_ns < state->min_capture_ns) return;
    state->last_capture_raw_ns = capture_started;
    timer.active = true;
    previous = state->cache; generation = state->generation; next_body = state->next_body_id;
  }
  try {
    if (capture_started <= 0 || cpu_started <= 0) throw std::runtime_error("native_observation_clock_failed");
    auto snapshot = std::make_shared<NativeSnapshot>();
    snapshot->ros_ns = captured_ros_ns; snapshot->raw_ns = capture_started; snapshot->generation = generation;
    // One graph-locked pose batch. The caller's Node mutex excludes local insertion.
    const auto submaps = map_builder_->pose_graph()->GetAllSubmapData();
    if (submaps.size() > state->max_submaps) throw std::length_error("native_submap_cap");
    std::map<NativeKey, std::shared_ptr<const NativeBody>> next_cache;
    std::size_t update_cells = 0, cached_cells = 0; std::uint64_t new_copies = 0;
    std::ostringstream pose_signature;
    for (const auto& entry : submaps) {
      const NativeKey key{entry.id.trajectory_id, entry.id.submap_index};
      const auto* submap = dynamic_cast<const cartographer::mapping::Submap2D*>(entry.data.submap.get());
      if (!submap) throw std::runtime_error("native_2d_submap_required");
      const auto* grid = dynamic_cast<const cartographer::mapping::ProbabilityGrid*>(submap->grid());
      if (!grid) throw std::runtime_error("native_probability_grid_required");
      std::shared_ptr<const NativeBody> body;
      const auto old = previous.find(key);
      if (old != previous.end() && old->second->source == submap && old->second->grid_source == grid &&
          old->second->version == submap->num_range_data() && old->second->finished == submap->insertion_finished()) {
        body = old->second;
      } else {
        Eigen::Array2i offset; cartographer::mapping::CellLimits limits;
        grid->ComputeCroppedLimits(&offset, &limits);
        if (limits.num_x_cells <= 0 || limits.num_y_cells <= 0 || offset.x() < 0 || offset.y() < 0)
          throw std::runtime_error("invalid_native_crop");
        const std::size_t cells = std::size_t(limits.num_x_cells) * std::size_t(limits.num_y_cells);
        if (cells > state->max_update_cells - update_cells) throw std::length_error("native_update_cell_cap");
        update_cells += cells;
        const auto& allocated = grid->limits().cell_limits();
        if (std::uint64_t(offset.x()) + std::uint64_t(limits.num_x_cells) > std::uint64_t(allocated.num_x_cells) ||
            std::uint64_t(offset.y()) + std::uint64_t(limits.num_y_cells) > std::uint64_t(allocated.num_y_cells))
          throw std::runtime_error("native_crop_outside_grid");
        auto fresh = std::make_shared<NativeBody>();
        fresh->key = key; fresh->source = submap; fresh->grid_source = grid;
        fresh->version = submap->num_range_data(); fresh->finished = submap->insertion_finished();
        fresh->i0 = offset.x(); fresh->i1 = offset.y(); fresh->n0 = limits.num_x_cells; fresh->n1 = limits.num_y_cells;
        fresh->resolution = grid->limits().resolution(); fresh->max_x = grid->limits().max().x(); fresh->max_y = grid->limits().max().y();
        if (!std::isfinite(fresh->resolution) || fresh->resolution <= 0 || !std::isfinite(fresh->max_x) || !std::isfinite(fresh->max_y))
          throw std::runtime_error("invalid_native_limits");
        fresh->local_pose = NativePose(submap->local_pose()); fresh->body_id = next_body++;
        fresh->probabilities.reserve(cells * 4); fresh->known.reserve(cells);
        for (int i1 = 0; i1 < fresh->n1; ++i1) for (int i0 = 0; i0 < fresh->n0; ++i0) {
          const Eigen::Array2i index(offset + Eigen::Array2i(i0, i1));
          const bool known = grid->IsKnown(index); const float probability = known ? grid->GetProbability(index) : 0.f;
          if (!std::isfinite(probability) || probability < 0.f || probability > 1.f)
            throw std::runtime_error("invalid_native_probability");
          NativeFloat(probability, &fresh->probabilities); fresh->known.push_back(known ? 1 : 0);
          fresh->known_count += known;
        }
        fresh->fingerprint = NativeFingerprint(fresh->probabilities, fresh->known);
        body = std::move(fresh); ++new_copies;
      }
      if (body->cells() > state->max_cached_cells - cached_cells) throw std::length_error("native_cache_cell_cap");
      cached_cells += body->cells(); next_cache.emplace(key, body);
      const auto global = NativePose(entry.data.pose);
      pose_signature << key.first << ':' << key.second << ':' << global << ';';
      snapshot->entries.push_back(NativeEntry{body, global});
    }
    std::ostringstream source_times; source_times << '['; bool first = true;
    {
      absl::MutexLock lock(&mutex_);
      for (const auto& entry : local_slam_data_) {
        if (!first) source_times << ','; first = false;
        source_times << "{\"trajectory_id\":" << entry.first << ",\"time_100ns\":"
                     << entry.second->time.time_since_epoch().count() << '}';
      }
    }
    source_times << ']'; snapshot->source_times = source_times.str();
    snapshot->capture_wall_ns = NativeRawNow() - capture_started;
    snapshot->capture_thread_cpu_ns = NativeThreadCpuNow() - cpu_started;
    snapshot->copied_bytes = 5 * update_cells;
    std::lock_guard<std::mutex> lock(state->mutex);
    if (state->cancelled || state->generation != generation) return;
    const auto signature = pose_signature.str();
    if (signature != state->pose_signature) { ++state->transform_revision; state->pose_signature = signature; }
    snapshot->transform_revision = state->transform_revision; snapshot->sequence = ++state->captures;
    state->copies += new_copies; state->copied_cells += update_cells; state->next_body_id = next_body;
    state->cache = std::move(next_cache); state->error.clear();
    if (state->pending) ++state->superseded;
    state->pending = std::move(snapshot);
  } catch (const std::exception& error) {
    std::lock_guard<std::mutex> lock(state->mutex);
    ++state->errors; state->error = dynamic_cast<const std::length_error*>(&error) ? "native_resource_limit" : "native_capture_failed";
    state->limited = bool(dynamic_cast<const std::length_error*>(&error));
    ++state->generation; state->pending.reset(); state->inflight.reset();
    state->last_completed_bodies.clear(); state->status_pending = true;
  }
}

namespace {
std::string NativePrefix(const NativeGridPublisherState& state, const char* kind) {
  std::ostringstream out;
  out << "{\"schema\":\"ubts.cartographer.native_probability_submaps.v1\",\"kind\":" << NativeJson(kind)
      << ",\"publisher_epoch\":" << NativeJson(state.epoch) << ",\"generation\":" << state.generation
      << ",\"map_frame\":" << NativeJson(state.frame)
      << ",\"values_semantics\":\"native_float32_from_quantized_probability_grid\""
         ",\"source_kind\":\"cartographer_node_state\",\"source_session\":null"
         ",\"live_sensor_admission_qualified\":false,\"runtime_authority\":false,\"physical_outputs\":false";
  return out.str();
}
std::string NativeManifest(const NativeGridPublisherState& state) {
  const auto& snapshot = *state.inflight; std::ostringstream out; out << std::setprecision(17);
  out << NativePrefix(state, "manifest") << ",\"sequence\":" << snapshot.sequence
      << ",\"transform_revision\":" << snapshot.transform_revision
      << ",\"captured_ros_ns\":" << snapshot.ros_ns << ",\"captured_RAW_ns\":" << snapshot.raw_ns
      << ",\"capture_body_under_Node_mutex_wall_ns\":" << snapshot.capture_wall_ns
      << ",\"capture_body_under_Node_mutex_thread_cpu_ns\":" << snapshot.capture_thread_cpu_ns
      << ",\"native_cell_bytes_copied\":" << snapshot.copied_bytes
      << ",\"full_refresh\":" << (state.full_refresh ? "true" : "false")
      << ",\"source_stamp_role\":\"latest_local_SLAM_observation_not_grid_insertion_or_callback\""
         ",\"local_SLAM_time_100ns\":" << snapshot.source_times << ",\"submaps\":[";
  bool first = true;
  for (std::size_t i = 0; i < snapshot.entries.size(); ++i) {
    const auto& entry = snapshot.entries[i]; const auto& body = *entry.body;
    if (!first) out << ','; first = false;
    const bool included = std::find(state.send_entries.begin(), state.send_entries.end(), i) != state.send_entries.end();
    out << "{\"trajectory_id\":" << body.key.first << ",\"submap_index\":" << body.key.second
        << ",\"native_version\":" << body.version << ",\"finished\":" << (body.finished ? "true" : "false")
        << ",\"body_id\":" << body.body_id << ",\"body_included\":" << (included ? "true" : "false")
        << ",\"cells\":" << body.cells() << ",\"known_cells\":" << body.known_count
        << ",\"body_fingerprint_fnv64\":" << NativeJson(body.fingerprint)
        << ",\"probability_encoding\":\"float32_little_endian\",\"known_encoding\":\"uint8_0_or_1\""
           ",\"flat_index\":\"i0+n0*i1\",\"crop_offset_i0_i1\":[" << body.i0 << ',' << body.i1
        << "],\"crop_dimensions_n0_n1\":[" << body.n0 << ',' << body.n1 << "],\"resolution\":" << body.resolution
        << ",\"native_max_xy\":[" << body.max_x << ',' << body.max_y << "],\"T_local_submap\":" << body.local_pose
        << ",\"T_map_submap\":" << entry.global_pose << '}';
  }
  out << "]}"; return out.str();
}
std::string NativeStatus(const NativeGridPublisherState& state) {
  std::ostringstream out; out << NativePrefix(state, "status")
    << ",\"cancelled\":" << (state.cancelled ? "true" : "false") << ",\"limited\":" << (state.limited ? "true" : "false")
    << ",\"error\":" << NativeJson(state.error) << ",\"captured_batches\":" << state.captures
    << ",\"native_grid_copies\":" << state.copies << ",\"native_cells_copied\":" << state.copied_cells
    << ",\"superseded_pending_states\":" << state.superseded << ",\"completed_producer_batches\":" << state.completed
    << ",\"publication_packets\":" << state.packets << ",\"capture_errors\":" << state.errors
    << ",\"last_capture_call_under_Node_mutex_wall_ns\":" << state.last_capture_call_wall_ns
    << ",\"last_capture_call_under_Node_mutex_thread_cpu_ns\":" << state.last_capture_call_thread_cpu_ns
    << ",\"minimum_capture_period_ns\":" << state.min_capture_ns
    << ",\"pending_states\":" << (state.pending ? 1 : 0) << ",\"inflight_states\":" << (state.inflight ? 1 : 0)
    << ",\"source_loss_qualified\":false,\"max_submaps\":" << state.max_submaps
    << ",\"max_update_cells\":" << state.max_update_cells << ",\"max_cached_cells\":" << state.max_cached_cells << '}';
  return out.str();
}
}  // namespace

void MapBuilderBridge::PublishNativeProbabilitySubmaps() {
  const auto state = native_grid_publisher_;
  if (!state) return;
  std::unique_lock<std::mutex> pump(state->pump_mutex, std::try_to_lock);
  if (!pump.owns_lock()) return;
  // Caller released Node's sensor mutex. No new thread or unbounded packet queue.
  try {
    for (std::size_t packet = 0; packet < state->packets_per_tick; ++packet) {
      std_msgs::msg::String message;
      rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher;
      std::uint64_t generation;
      std::shared_ptr<const NativeSnapshot> completes_batch;
      {
        std::lock_guard<std::mutex> lock(state->mutex);
        generation = state->generation; publisher = state->publisher;
        if (!publisher) return;
        if (state->status_pending) {
          message.data = NativeStatus(*state); state->status_pending = false;
        } else {
          if (state->cancelled || state->limited) return;
          if (!state->inflight) {
            if (!state->pending) return;
            state->inflight = std::move(state->pending); state->manifest_pending = true;
            state->full_refresh = state->completed % state->full_refresh_batches == 0;
            state->send_entries.clear(); state->send_index = state->send_offset = 0;
            for (std::size_t i = 0; i < state->inflight->entries.size(); ++i) {
              const auto& body = *state->inflight->entries[i].body;
              const auto old = state->last_completed_bodies.find(body.key);
              if (state->full_refresh || old == state->last_completed_bodies.end() || old->second != body.body_id)
                state->send_entries.push_back(i);
            }
          }
          if (state->manifest_pending) {
            message.data = NativeManifest(*state); state->manifest_pending = false;
          } else if (state->send_index < state->send_entries.size()) {
            const auto& body = *state->inflight->entries[state->send_entries[state->send_index]].body;
            const auto count = std::min(state->chunk_cells, body.cells() - state->send_offset);
            std::ostringstream out; out << NativePrefix(*state, state->compress_chunks ? "cells_zlib" : "cells") << ",\"sequence\":" << state->inflight->sequence
              << ",\"body_id\":" << body.body_id << ",\"offset_cells\":" << state->send_offset << ",\"count_cells\":" << count;
            if (state->compress_chunks) {
              out << ",\"cell_bytes_zlib_base64\":" << NativeJson(NativeCompressedChunk(
                  body.probabilities.data() + 4 * state->send_offset,
                  body.known.data() + state->send_offset, count)) << '}';
            } else {
              out << ",\"probability_f32le_base64\":" << NativeJson(NativeBase64(body.probabilities.data() + 4 * state->send_offset, 4 * count))
                  << ",\"known_u8_base64\":" << NativeJson(NativeBase64(body.known.data() + state->send_offset, count)) << '}';
            }
            message.data = out.str(); state->send_offset += count;
            if (state->send_offset == body.cells()) { ++state->send_index; state->send_offset = 0; }
          } else {
            std::ostringstream out; out << NativePrefix(*state, "done") << ",\"sequence\":" << state->inflight->sequence
              << ",\"transform_revision\":" << state->inflight->transform_revision
              << ",\"producer_batch_complete\":true,\"subscriber_delivery_qualified\":false}";
            message.data = out.str();
            completes_batch = state->inflight;
          }
        }
        if (message.data.size() > 1024u * 1024u) throw std::length_error("native_packet_cap");
      }
      {
        std::lock_guard<std::mutex> lock(state->mutex);
        if (generation != state->generation) return;
        // A cancelled status may be published; no later new data is generated.
        if (state->cancelled && message.data.find("\"kind\":\"status\"") == std::string::npos) return;
      }
      message.data.pop_back();
      message.data += ",\"published_ROS_now_ns\":" + std::to_string(state->clock->now().nanoseconds()) +
                      ",\"publication_clock_role\":\"sampled_before_RMW_publish_not_source_stamp\"}";
      if (message.data.size() > 1024u * 1024u) throw std::length_error("native_packet_cap");
      publisher->publish(message);
      {
        std::lock_guard<std::mutex> lock(state->mutex);
        ++state->packets;
        if (completes_batch && generation == state->generation && state->inflight == completes_batch) {
          state->last_completed_bodies.clear();
          for (const auto& entry : completes_batch->entries)
            state->last_completed_bodies[entry.body->key] = entry.body->body_id;
          state->inflight.reset(); ++state->completed; state->status_pending = true;
        }
      }
    }
  } catch (const std::exception&) {
    std::lock_guard<std::mutex> lock(state->mutex);
    ++state->errors; state->error = "native_publication_failed"; ++state->generation;
    state->pending.reset(); state->inflight.reset(); state->last_completed_bodies.clear(); state->status_pending = true;
  }
}

void MapBuilderBridge::CancelNativeProbabilityPublisher() {
  StopDerivedDisplayCapture();
  const auto state = native_grid_publisher_;
  if (!state) return;
  std::lock_guard<std::mutex> lock(state->mutex);
  state->cancelled = true; ++state->generation; state->pending.reset(); state->inflight.reset();
  state->cache.clear(); state->last_completed_bodies.clear(); state->status_pending = true;
}

// Private DEFAULT-OFF derived display. Runtime owns SDK-free copied bytes only.
namespace derived = ::ubts::research::derived_catalog;
namespace {
std::array<double,7> DerivedPose(const cartographer::transform::Rigid3d& pose) {
  const auto& t=pose.translation();const auto& q=pose.rotation();
  if(!t.allFinite()||!q.coeffs().allFinite())throw std::runtime_error("nonfinite_derived_native_pose");
  return {{t.x(),t.y(),t.z(),q.x(),q.y(),q.z(),q.w()}};
}
struct DerivedSdkIdentity {
  const cartographer::mapping::Submap* submap=nullptr;
  const cartographer::mapping::Grid2D* grid=nullptr; // comparison only, NEVER deferred reads
  int native_version=0;bool finished=false;derived::Request request;
};
}
struct DerivedDisplayCaptureState {
  explicit DerivedDisplayCaptureState(std::shared_ptr<derived::Runtime> owner):runtime(std::move(owner)){}
  const std::shared_ptr<derived::Runtime> runtime;
  std::mutex identities_mutex;std::map<derived::Key,DerivedSdkIdentity> identities;
  std::uint64_t next_body_id=0;
};
std::shared_ptr<DerivedDisplayCaptureState> MapBuilderBridge::DerivedDisplayState()const {
  std::lock_guard<std::mutex> setup(derived_setup_mutex_);return derived_display_capture_;
}
void MapBuilderBridge::BootstrapDerivedDisplayCapture(derived::Bootstrap dependencies,
    std::shared_ptr<derived::Runtime>* external_owner) {
  std::lock_guard<std::mutex> setup(derived_setup_mutex_);
  if(derived_setup_sealed_)throw std::runtime_error("derived_setup_already_sealed_before_callbacks");
  if(!dependencies.enabled)return; // no providers, legacy CLI/default unchanged
  if(native_grid_publisher_||derived_display_capture_)throw std::runtime_error("derived_legacy_modes_mutually_exclusive");
  if(!external_owner||*external_owner)throw std::runtime_error("empty_external_archive_owner_required");
  auto runtime=std::make_shared<derived::Runtime>(std::move(dependencies));
  auto state=std::make_shared<DerivedDisplayCaptureState>(runtime);
  *external_owner=runtime;derived_display_capture_=std::move(state);
}
void MapBuilderBridge::SealDerivedDisplaySetup() {
  std::lock_guard<std::mutex> setup(derived_setup_mutex_);derived_setup_sealed_=true;
}
std::shared_ptr<derived::Runtime> MapBuilderBridge::DerivedDisplayRuntime()const {
  const auto state=DerivedDisplayState();return state?state->runtime:nullptr;
}
bool MapBuilderBridge::DerivedDisplayCaptureEnabled()const{return bool(DerivedDisplayState());}
derived::State MapBuilderBridge::CaptureDerivedDisplaySubmaps(std::int64_t captured_ros_ns) {
  const auto state=DerivedDisplayState();if(!state)return derived::State::OFF;
  const auto runtime=state->runtime;
  try {
    auto guard=runtime->begin_capture();
    if(!guard)return runtime->stopped()?derived::State::STOPPED:derived::State::DEFERRED;
    // Backpressure is BEFORE SDK metadata. Actual Node caller owns sensor mutex.
    std::lock_guard<std::mutex> identities(state->identities_mutex);
    derived::Frame frame;std::vector<derived::Request> requests;
    frame.context=runtime->status().coordinator.context;
    frame.sequence=guard.sequence();frame.batch_id=guard.sequence(); // copied-batch ID, not graph revision
    frame.observation.captured_RAW_ns=NativeRawNow();frame.observation.captured_ROS_ns=captured_ros_ns;
    if(frame.observation.captured_RAW_ns<=0||captured_ros_ns<0)throw std::runtime_error("derived_observation_clock_failed");
    const auto submaps=map_builder_->pose_graph()->GetAllSubmapData(); // one copied batch
    if(submaps.size()>4096)return derived::State::DEFERRED;
    std::map<derived::Key,DerivedSdkIdentity> next;
    for(const auto& entry:submaps) {
      const derived::Key key{entry.id.trajectory_id,entry.id.submap_index};
      const auto* submap=dynamic_cast<const cartographer::mapping::Submap2D*>(entry.data.submap.get());
      if(!submap)throw std::runtime_error("derived_2d_submap_required");
      const auto* grid=dynamic_cast<const cartographer::mapping::ProbabilityGrid*>(submap->grid());
      if(!grid||submap->num_range_data()<0)throw std::runtime_error("derived_native_grid_required");
      const auto old=state->identities.find(key);derived::Request request;
      if(old!=state->identities.end()&&old->second.submap==submap&&old->second.grid==grid&&
          old->second.native_version==submap->num_range_data()&&old->second.finished==submap->insertion_finished()) {
        request=old->second.request;
      }else {
        if(old==state->identities.end()&&state->identities.size()>=4096)return derived::State::DEFERRED;
        Eigen::Array2i offset;cartographer::mapping::CellLimits crop;grid->ComputeCroppedLimits(&offset,&crop);
        const auto& allocated=grid->limits().cell_limits();
        if(offset.x()<0||offset.y()<0||crop.num_x_cells<=0||crop.num_y_cells<=0||
            std::uint64_t(offset.x())+std::uint64_t(crop.num_x_cells)>std::uint64_t(allocated.num_x_cells)||
            std::uint64_t(offset.y())+std::uint64_t(crop.num_y_cells)>std::uint64_t(allocated.num_y_cells))
          throw std::runtime_error("derived_native_crop_outside_grid");
        if(std::uint64_t(crop.num_x_cells)*std::uint64_t(crop.num_y_cells)>4194304)return derived::State::DEFERRED;
        derived::BodyRef body;body.context=frame.context;body.key=key;
        body.version=std::uint64_t(submap->num_range_data());body.finished=submap->insertion_finished();
        if(state->next_body_id==UINT64_MAX)throw std::runtime_error("derived_body_id_overflow");
        body.body_id=++state->next_body_id;
        body.geometry.i0=std::uint32_t(offset.x());body.geometry.i1=std::uint32_t(offset.y());
        body.geometry.n0=std::uint32_t(crop.num_x_cells);body.geometry.n1=std::uint32_t(crop.num_y_cells);
        body.geometry.limits={{grid->limits().resolution(),grid->limits().max().x(),grid->limits().max().y()}};
        body.geometry.local_pose=DerivedPose(submap->local_pose());
        const auto result=runtime->capture_raw(guard,body,[&](derived::Writer& writer) {
          for(int i1=0;i1<crop.num_y_cells;++i1)for(int i0=0;i0<crop.num_x_cells;++i0) {
            const Eigen::Array2i index(offset+Eigen::Array2i(i0,i1));const bool known=grid->IsKnown(index);
            writer.cell(known?grid->GetProbability(index):0.f,known);
          }
        },&request);
        if(result!=derived::State::ACCEPTED&&result!=derived::State::UNCHANGED)return result;
      }
      next.emplace(key,DerivedSdkIdentity{submap,grid,submap->num_range_data(),submap->insertion_finished(),request});
      state->identities[key]=next.at(key); // retained on LATER partial-batch DEFER
      requests.push_back(request);frame.entries.push_back(derived::Entry{request.body,DerivedPose(entry.data.pose)});
    }
    { // Local SLAM stamps remain a separate observation role.
      absl::MutexLock local(&mutex_);
      if(local_slam_data_.size()>4096)return derived::State::DEFERRED;
      for(const auto& item:local_slam_data_)
        frame.observation.latest_local_slam_ticks.emplace(item.first,item.second->time.time_since_epoch().count());
    }
    const auto result=runtime->submit(guard,std::move(frame),std::move(requests));
    if(result==derived::State::ACCEPTED)state->identities=std::move(next);
    return result;
  }catch(const std::bad_alloc&){return runtime->stopped()?derived::State::STOPPED:derived::State::DEFERRED;}
  catch(const std::length_error&){return runtime->stopped()?derived::State::STOPPED:derived::State::DEFERRED;}
  catch(const std::exception&) {
    if(runtime->stopped())return derived::State::STOPPED;
    runtime->fatal_source_fault();return derived::State::REJECTED;
  }
}
derived::State MapBuilderBridge::FinalizeDerivedDisplayCapture() {
  const auto runtime=DerivedDisplayRuntime();return runtime?runtime->pump():derived::State::OFF;
}
derived::RuntimeStatus MapBuilderBridge::DerivedDisplayStatus()const {
  const auto runtime=DerivedDisplayRuntime();return runtime?runtime->status():derived::Runtime{}.status();
}
derived::State MapBuilderBridge::CloseDerivedDisplayCapture() {
  const auto runtime=DerivedDisplayRuntime();return runtime?runtime->close():derived::State::OFF;
}
void MapBuilderBridge::StopDerivedDisplayCapture() {
  const auto runtime=DerivedDisplayRuntime();if(runtime)runtime->stop(); // no digest/provider/SDK lock wait
}

}  // namespace cartographer_ros
