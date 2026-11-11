# C0 observation source consolidation — in progress

This branch consolidates a Python ICP observation and display-map baseline. Its source includes the original-CDR observer, `PhysicalNativeShadowFactory`, the Python localization kernel, the asynchronous accepted-map worker, the bounded map transport, and the FIELD10 observation adapter. This C0 name does not identify the separate offline Cartographer mapping baseline or the ARM native SDK actor.

The [92-file source manifest](C0_SOURCE_SHA256.json) records SHA-256 and size over exact file bytes. The source was assembled on public base `337213997b7e306b7f38d336adc17c2aa9de5939`. This is an in-progress source consolidation, rather than a complete deployed-image reconstruction. Source imports, algorithms, ownership fences, original timestamps, source/RAW freshness checks, and observation-only output boundaries remain explicit in the code. The package makes no mapping-accuracy, control-ready, full-autonomy, or physical qualification claim.

The manifest omits compiled binaries, runtime build metadata, recordings, GNSS records, credentials, personal settings, and the unverified HTML entry body. The inherited HTML is still a base-source file; its equivalence to an installed UI and the client it loads are unverified. Ten app imports remain inherited from the base without a matching installed-source pin in this consolidation:

- `dashboard_server/autonomy_settings.py`
- `dashboard_server/field_preflight.py`
- `dashboard_server/gps.py`
- `dashboard_server/mapping_operations.py`
- `dashboard_server/offline_maps.py`
- `dashboard_server/record_based_autonomy.py`
- `dashboard_server/rover_api.py`
- `dashboard_server/source_live_mapping.py`
- `dashboard_server/telemetry.py`
- `dashboard_server/window_cells.py`

The selected local regression covered synthetic observation loops, injected lifecycle resources, a mocked socket client, and two pure tile-transport checks: 56 tests and 67 subtests passed. It did not start hardware, ROS, a native actor, an external API, or a real service. Reproducible builds, whole-image dependency closure, installation, sustained map-display rate and latency, and field behavior remain unqualified. The source's sensor-admission conditions and a UI's display-update target are separate contracts.

`deploy/native_sensor_stream/keyframe_registration.py` and `static/app.js` have explicit `-text` attributes so Git preserves their hash-checked CRLF bodies. The former is referenced by `NATIVE_MAP_SOURCE.json`. No source formatting or line-ending normalization is part of this consolidation. The manifest describes source bytes; it grants no runtime permission or feature enablement.

Original copyright headers, existing license/notice files, and vendor source metadata are retained. The FIELD10 vendor manifest records its source commit and the original five vendored file hashes. This consolidation does not rewrite those notices or their attribution.
