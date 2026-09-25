# Rover Control Dashboard

ESP8266 manual control uses an automatic Dashboard session and `/run/ubts-rover/control.sock`, with no separate control password. Switch control ON to use WASD, diagonal chords, and Q/E speed steps of 5 percentage points. The slider also applies during motion. See [ESP8266 control](esp_rover_control.md) for wheel mixing, ownership, stop behavior, USB mapping, and low-bandwidth profiles.

Live camera, LiDAR, IMU and rosbag2 recording/archive controls remain on `/rover-control`. Preview scaling does not change ROS recording inputs.
