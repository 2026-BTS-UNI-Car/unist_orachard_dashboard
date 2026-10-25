# Observation and map-display polling integration

This branch combines two polling changes with the current Python ICP observation and display-map source baseline. It does not replace that baseline with the separate native SDK or offline Cartographer mapper.

The observation worker includes provider and processing time in its configured polling period. Its default 50 ms period is unchanged. Empty-input waiting still uses the existing wake event, so an enqueued input can wake the worker immediately. The current sealed-input reader, diagnostics, source/RAW freshness checks, post-plan validation, ownership fences, and Stop behavior remain in place.

The native accepted-map client includes the asynchronous request, paging, and lease-release work in its configured period, whose default remains 500 ms. An overrun schedules the next attempt without a catch-up burst. The existing single-flight, revision, abort, lease, tile, and last-good-map behavior remains unchanged.

The [92-file manifest](C0_SOURCE_SHA256.json) changes only the observation-worker entry; the other 91 entries retain their exact bytes. The native-map module is outside that manifest and has a separate canonical Git-byte hash in the [integration source proof](OBSERVATION_UI_INTEGRATION_SOURCE.json). The two raw-byte and seven LF Git attribute rules are unchanged. Reversing the small timing edits recovers each parent source exactly. The tests use the current worker and actual asynchronous client class, with synthetic inputs and clocks.

One local regression run passed 51 Python tests and 37 subtests, nine asynchronous map-client cases, and the existing pose-history notice check. These checks cover empty-input enqueue wake-up, processing overruns, slow or failed requests, Stop races, cancellation, and retained history. They did not run a device, ROS, a native actor, or an external service.

This remains an in-progress source integration. Reproducible builds, complete deployed-image dependency closure, installation, field behavior, mapping accuracy, control readiness, and full autonomy are unqualified. The installed HTML body and which client it loads are unverified, and the ten inherited app imports listed in the [source consolidation note](C0_SOURCE_CONSOLIDATION.md) remain unpinned. No live map-display latency gain has been measured. Sensor-admission freshness and a display-update target are separate contracts.

Original copyright headers, licenses, notices, and vendor source metadata are retained.
