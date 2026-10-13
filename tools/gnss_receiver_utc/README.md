# Receiver UTC evidence and original packet retention

Source-only, no Jetson deployment. The shared GPS decoder preserves the exact
validated100B NAV-PVT input frame and its92B payload plus raw calendar, validity
bits, signed nano, integer tAcc and iTOW. Direct payload calls remain explicitly
payload-only; packets are never regenerated and described as original input.
Existing gps_time, fix quality, serial configuration and host headers stay intact.
The ROS driver adds a publication-role label; it does not change the host clock.

The pure helper independently checks checksum, raw/decoded agreement, valid UTC,
integer nanoseconds, explicit UTC timezone, ordering and supported dates. Leap
second60 is retained raw but rejected, without clamping or an invented offset.
Valid receiver time can exist without a valid position fix and supplies no factor.

ReceiverUTCAdmissions is defaultOFF and bounded. It imports the ACTUAL public
native OriginalGNSS/CDR parser namespace, requires an external original-pair
custody check AND its own full packet validation, and retains publication header
separately. Host measurement time remains unknown. Stop and bounded commit use
one lock; parsing and external custody stay outside it. This is no hardware,
measurement-clock association, GPS yaw, position factor or autonomous authority.

The seven private compact October10 extracts contain3366 rows but omit raw UTC
quality and cannot regain that evidence. Their original recordings may contain
more fields; that presence has not been verified. Public tests use synthetic
UBX/CDR packets and the actual public module classes; no private location data is
committed. Run python -B -m unittest tools.gnss_receiver_utc.test_utc from the
repository root. They do not start serial, ROS, remote services or clock writes.

Still open: live original source custody, receiver UTC to LiDAR/IMU time relation,
mount/fixed-frame uncertainty and conservative GPS factor admission. Ordinary
5–10m GPS precision is not orchard-row or yaw truth. No missing historical flags
or packet bytes are reconstructed and no mapping factor is enabled by this code.
