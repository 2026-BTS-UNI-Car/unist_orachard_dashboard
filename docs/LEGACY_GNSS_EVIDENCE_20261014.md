# Recorded GNSS evidence and clock limits, 2026-10-14

Seven October 3 gps.jsonl copies match their original archive manifests. Their
3,169 rows are a retained subset of6,207 rosbag /gps/data messages. Those counts
must stay separate; standalone logs cannot certify the complete ROS population.

A bounded262,154-byte compressed prefix read SHA-joined four original GPS String
CDRs and four Fix CDRs to the original ledger. All four String records have an
exact gps_link Fix-header counterpart with approximated covariance type1. Only
two occur in the standalone log. The whole gzip was not freshly rehashed and the
large bag/originals were not reread; this is a bounded sample audit.

All3,169 log rows and the four CDR String samples lack UTC validity, fullyResolved,
signed nano, tAcc, original NAV-PVT packet and stamp_role. They retain whole-second
gps_time, iTOW and host stamps, which do not justify precise receiver measurement
time or LiDAR/IMU clock association. Nothing reconstructs those absent fields.
The October 10 original carriers remain unverified; compact extracts do not
establish their absence in every original message.

The new additive ACTUAL/SIM decoder preserves validated input NAV-PVT packets
for future recordings. Its default-OFF UTC admission is not a GPS mapping factor,
clock repair or yaw estimate. Current mapping work remains LiDAR/IMU first;
ordinary5-10m GNSS precision does not establish orchard-row position or heading.
Receiver time/transport delay, fixed-frame/mount uncertainty and conservative
factor admission require their own evidence before fusion is enabled.

The separately qualified Linux NumPy helper is also not the actual SIM server.
Existing inspected launch profiles use other images and non-isolated ROS Python
entrypoints; the source mapping imports NumPy before overview initialization.
The real current SIM runtime was not queried. A dedicated fresh writer/overview
process needs actual shared-owner/provider/ACK/Stop/lease wiring and environment
qualification, with no owner identity fabricated from IPC metadata.

Sanitized audit summary:

```json
{
  "original_JSON_records": 7,
  "original_JSON_rows": 3169,
  "rosbag_GPS_data_rows": 6207,
  "all_seven_log_SHA_match_original_archive_manifests": true,
  "original_CDR_prefix": {
    "compressed_bytes": 262154,
    "expanded_bytes": 728734,
    "GPS_CDRs": 8,
    "String_data": 4,
    "Fix": 4,
    "exact_original_SHA_ledger_joins": 8,
    "exact_host_header_counterparts": 4,
    "exact_standalone_log_sample_matches": 2,
    "full_gzip_SHA_not_freshly_reverified": true,
    "full_bag_not_reread": true
  },
  "missing_strong_UTC_raw_fields": [
    "gps_time_valid_date",
    "gps_time_valid_time",
    "gps_time_fully_resolved",
    "gps_time_fraction_ns",
    "gps_time_accuracy_s",
    "gps_time_accuracy_ns",
    "gps_time_valid_bits",
    "gps_time_calendar_utc",
    "stamp_role",
    "receiver_pvt_frame_hex",
    "receiver_pvt_payload_hex",
    "receiver_pvt_frame_sha256"
  ],
  "Oct10_original_raw_carrier_fields_still_unverified": true,
  "handoff_SHA256": "7eb66cb85aa351ff5133d33cbffb94dfd7e2bd368f18b0380a51f75fc838ccb8",
  "mapping_factor_or_clock_association": false,
  "private_coordinates_or_raw_CDR_exported": false
}
```
