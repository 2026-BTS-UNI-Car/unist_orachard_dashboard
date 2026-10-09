"""Build-time binding of new stream sources and the compiled observer.

This manifest is an inventory, not independent evidence that a compiler ran.
The isolated build transcript, immutable image and ROOT audit supply that proof.
"""
import hashlib
import json
from pathlib import Path


FILES = (
    "stream_runner.py", "stream_rows.py", "dashboard_server/native_owner_lifecycle.py",
    "observer-src/actual_cdr_stream.cpp", "observer-src/CMakeLists.txt", "OBSERVER_CONTRACT.json",
)
PACKAGE_SHA = "fb57299a084f4b16a90abb31a627048b4a8b11175bb260089cd0e69ae0aa1ed3"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parent
    contract = json.loads((root / "OBSERVER_CONTRACT.json").read_text())
    sources = {name: digest(root / name) for name in FILES}
    assert contract["schema"] == "ubts.actual.native_stream.observer_contract.v1"
    assert all(sources[name] == value for name, value in contract["source_sha256"].items())
    assert digest(Path("/opt/ubts-shadow/package/HANDOFF.json")) == PACKAGE_SHA
    manifest = dict(schema="ubts.actual.native_stream.build.v1", files_sha256=sources,
        observer_contract_sha256=sources["OBSERVER_CONTRACT.json"],
        observer_binary_sha256=digest(root / "bin/actual_cdr_stream"),
        physical_package_manifest_sha256=PACKAGE_SHA,
        runtime_authority=False, motor_command_sent=False, autonomy_output_enabled=False,
        mission_admission=False, production_mapping_integration_pending=True)
    raw = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    (root / "BUILD_PROVENANCE.json").write_bytes(raw)
    print(json.dumps(dict(build_manifest_sha256=hashlib.sha256(raw).hexdigest(), **manifest)))


if __name__ == "__main__":
    main()
