"""The images must contain every module their entry points import.

Dockerfile.ros2 copies scripts/ file by file. When a script gains a new sibling import and the COPY list is not
updated, the image builds but the service dies on start (this happened with recording_durability.py,
ros_executor.py and camera_stream_policy.py). This test follows the imports of every compose command, statically,
and checks that each needed file under scripts/ is copied into the image that runs it. It also checks that every
COPY source in the root Dockerfiles exists, so removing a file cannot silently break a build.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("scripts", "dashboard_server")


def compose_services() -> dict[str, dict[str, str]]:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"^  ([a-z0-9][a-z0-9-]*):\n(.*?)(?=^  [a-z0-9][a-z0-9-]*:\n|\Z)", text, re.S | re.M):
        name, body = match.group(1), match.group(2)
        dockerfile = re.search(r"^      dockerfile:\s*(\S+)", body, re.M)
        has_build = re.search(r"^    build:", body, re.M) is not None
        services[name] = {"dockerfile": (dockerfile.group(1) if dockerfile else "Dockerfile") if has_build else "",
                          "body": body}
    return services


def entry_modules(command: str) -> set[str]:
    found = {f"scripts.{m}" for m in re.findall(r"/app/scripts/([A-Za-z0-9_]+)\.py", command)}
    found |= set(re.findall(r"python3?\s+(?:-B\s+)?-m\s+((?:scripts|dashboard_server)(?:\.[A-Za-z0-9_]+)*)", command))
    return found


def module_file(module: str) -> Path | None:
    parts = module.split(".")
    if parts[0] not in PACKAGES:
        return None
    base = ROOT.joinpath(*parts)
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    package = path.relative_to(ROOT).with_suffix("").parts[:-1]  # same for a module and a package __init__
    names: set[str] = set()
    for node in ast.walk(tree):  # imports inside functions count: they run when the service runs
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = list(package[: len(package) - node.level + 1])
                base = ".".join(anchor + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)  # `from pkg import submodule`
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__"
              and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            names.add(node.args[0].value)
    expanded: set[str] = set()
    for name in names:
        parts = name.split(".")
        expanded.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))  # parent packages run too
    return expanded


def closure(starts: set[str]) -> set[Path]:
    seen: set[Path] = set()
    todo = [m for m in starts]
    while todo:
        path = module_file(todo.pop())
        if path is None or path in seen:
            continue
        seen.add(path)
        todo.extend(imported_modules(path))
    return seen


def copied(dockerfile: str) -> tuple[set[str], list[str]]:
    """Repository paths a root Dockerfile copies (whole directories end with '/'), and every COPY source."""
    text = (ROOT / dockerfile).read_text(encoding="utf-8")
    sources: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        words = line.split()[1:]
        sources.extend(words[:-1])
    paths = {s.rstrip("/") + "/" if (ROOT / s).is_dir() else s for s in sources}
    return paths, sources


def is_copied(rel: str, paths: set[str]) -> bool:
    return rel in paths or any(p.endswith("/") and rel.startswith(p) for p in paths)


class ImageCopyTests(unittest.TestCase):
    def test_every_compose_entry_point_has_its_imports_in_the_image(self):
        services = compose_services()
        checked = 0
        problems: dict[str, list[str]] = {}
        for name, service in services.items():
            dockerfile = service["dockerfile"]
            if dockerfile not in {"Dockerfile", "Dockerfile.ros2"}:
                continue
            starts = entry_modules(service["body"])
            if dockerfile == "Dockerfile" and not starts:
                starts = {"dashboard_server.__main__"}  # the image's CMD
            self.assertTrue(starts, f"{name}: no entry point found in its command")
            paths, _ = copied(dockerfile)
            missing = sorted(
                p.relative_to(ROOT).as_posix() for p in closure(starts)
                if not is_copied(p.relative_to(ROOT).as_posix(), paths)
            )
            if missing:
                problems[f"{name} ({dockerfile})"] = missing
            checked += 1
        self.assertEqual(problems, {}, "entry points import files their image does not copy")
        self.assertGreaterEqual(checked, 9)

    def test_ros_image_default_command_has_its_imports(self):
        text = (ROOT / "Dockerfile.ros2").read_text(encoding="utf-8")
        cmd = [line for line in text.splitlines() if line.startswith("CMD ")][-1]
        starts = entry_modules(cmd)
        self.assertTrue(starts)
        paths, _ = copied("Dockerfile.ros2")
        missing = sorted(p.relative_to(ROOT).as_posix() for p in closure(starts)
                         if not is_copied(p.relative_to(ROOT).as_posix(), paths))
        self.assertEqual(missing, [])

    def test_every_copy_source_exists(self):
        for dockerfile in ("Dockerfile", "Dockerfile.ros2", "Dockerfile.lidar_driver"):
            _, sources = copied(dockerfile)
            self.assertTrue(sources, dockerfile)
            for source in sources:
                self.assertTrue((ROOT / source).exists(), f"{dockerfile} copies a missing path: {source}")


if __name__ == "__main__":
    unittest.main()
