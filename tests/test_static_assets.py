"""Content-hash addresses and caching of static files (dashboard_server/static_assets.py, NET-04)."""
import gzip
import hashlib
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_server.static_assets import LONG_CACHE, REVALIDATE, VERSION_LENGTH, StaticAssets  # noqa: E402

STATIC = ROOT / "static"
VERSIONED = re.compile(r"\?v=[0-9a-f]{%d}" % VERSION_LENGTH)


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def touch_later(path):
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))


class SmallTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, "index.html", '<link href="/static/css/a.css"><script src="/static/app/main.js"></script>'
                                        '<script type="module" src="/static/live.js"></script>'
                                        '<img src="/static/missing.png"><script src="/static/vendor/lib.js"></script>')
        write(self.root, "css/a.css", '@font-face{src:url("/static/fonts/f.woff2")}'
                                      '.i{background:url("data:image/svg+xml,%3Csvg%3E")} .j{background:url(../img/x.png)}')
        write(self.root, "fonts/f.woff2", "font")
        write(self.root, "img/x.png", "png")
        write(self.root, "app/main.js", "var x = 1;")
        write(self.root, "live.js", "import {b} from './b.mjs';\nexport {c} from \"./sub/c.mjs\";\n"
                                    "import './side.mjs';\nconst m = () => import('./lazy.mjs');\n"
                                    "const later = \"/static/vendor/lib.js\"; const style = `/static/x-${t}.json`;\n"
                                    "const gone = '/static/nothing.js';\n")
        write(self.root, "b.mjs", "import {c} from './sub/c.mjs';\nexport const b = c;\n")
        write(self.root, "sub/c.mjs", "export const c = 1;\n")
        write(self.root, "side.mjs", "globalThis.side = 1;\n")
        write(self.root, "lazy.mjs", "export default 1;\n")
        write(self.root, "vendor/lib.js", "var y = \"from './not-a-module.mjs'\";")
        self.assets = StaticAssets(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def body(self, rel):
        return self.assets.serve(rel, None, False).body.decode("utf-8")

    def test_page_names_each_file_with_the_hash_of_its_served_body(self):
        page = self.body("index.html")
        for rel in ("css/a.css", "app/main.js", "live.js", "vendor/lib.js"):
            served = self.assets.serve(rel, None, False)
            self.assertEqual(served.version, hashlib.sha256(served.body).hexdigest()[:VERSION_LENGTH])
            self.assertIn(f"/static/{rel}?v={served.version}", page)
        self.assertIn('<img src="/static/missing.png">', page, "a missing file keeps its plain address")

    def test_module_imports_and_css_urls_carry_versions(self):
        live = self.body("live.js")
        for rel, spec in (("b.mjs", "./b.mjs"), ("sub/c.mjs", "./sub/c.mjs"), ("side.mjs", "./side.mjs"),
                          ("lazy.mjs", "./lazy.mjs")):
            self.assertIn(f"{spec}?v={self.assets.version(rel)}", live)
        self.assertIn(f"'./sub/c.mjs?v={self.assets.version('sub/c.mjs')}'", self.body("b.mjs"))
        self.assertIn(f'"/static/vendor/lib.js?v={self.assets.version("vendor/lib.js")}"', live)
        self.assertIn("`/static/x-${t}.json`", live, "template strings stay as they are")
        self.assertIn("'/static/nothing.js'", live, "a missing file keeps its plain address")
        css = self.body("css/a.css")
        self.assertIn(f'url("/static/fonts/f.woff2?v={self.assets.version("fonts/f.woff2")}")', css)
        self.assertIn(f'url(../img/x.png?v={self.assets.version("img/x.png")})', css)
        self.assertIn('url("data:image/svg+xml,%3Csvg%3E")', css)
        self.assertEqual(self.body("vendor/lib.js"), "var y = \"from './not-a-module.mjs'\";",
                         "classic vendor scripts are sent unchanged")

    def test_changing_a_deep_module_changes_every_version_up_to_the_page(self):
        before = {rel: self.assets.version(rel) for rel in ("sub/c.mjs", "b.mjs", "live.js", "index.html", "app/main.js")}
        path = write(self.root, "sub/c.mjs", "export const c = 2;\n")
        touch_later(path)
        after = {rel: self.assets.version(rel) for rel in before}
        for rel in ("sub/c.mjs", "b.mjs", "live.js", "index.html"):
            self.assertNotEqual(before[rel], after[rel], rel)
        self.assertEqual(before["app/main.js"], after["app/main.js"])

    def test_same_content_keeps_its_version_after_a_new_mtime(self):
        version = self.assets.version("app/main.js")
        touch_later(self.root / "app/main.js")
        self.assertEqual(self.assets.version("app/main.js"), version)

    def test_cache_headers(self):
        version = self.assets.version("app/main.js")
        self.assertEqual(self.assets.serve("app/main.js", version, False).cache_control, LONG_CACHE)
        self.assertEqual(self.assets.serve("app/main.js", None, False).cache_control, REVALIDATE)
        self.assertEqual(self.assets.serve("app/main.js", "0" * VERSION_LENGTH, False).cache_control, REVALIDATE,
                         "an old version gets the current body but is checked again")
        self.assertEqual(self.assets.serve("index.html", None, False).cache_control, REVALIDATE)
        plain = self.assets.serve("app/main.js", None, False)
        packed = self.assets.serve("app/main.js", None, True)
        self.assertEqual(plain.etag, f'"{version}"')
        self.assertEqual(packed.etag, f'"{version}-gz"')

    def test_gzip_result_is_kept(self):
        first = self.assets.serve("live.js", None, True)
        second = self.assets.serve("live.js", None, True)
        self.assertTrue(first.gzip)
        self.assertIs(first.body, second.body)
        self.assertEqual(gzip.decompress(second.body), self.assets.serve("live.js", None, False).body)
        self.assertEqual(self.assets.stats["gzip_made"], 1)
        self.assertGreaterEqual(self.assets.stats["gzip_reused"], 1)
        self.assertFalse(self.assets.serve("img/x.png", None, True).gzip, "images are not gzipped")

    def test_gzip_cache_is_bounded(self):
        assets = StaticAssets(self.root, gzip_cache_bytes=200)
        for rel in ("live.js", "b.mjs", "sub/c.mjs", "side.mjs", "lazy.mjs", "css/a.css", "index.html"):
            assets.serve(rel, None, True)
        self.assertLessEqual(assets._gzip_bytes, 200)

    def test_warming_prepares_the_page_for_the_first_visitor(self):
        def page_filter(text):
            return text.replace("<link", '<link data-kind="x"', 1)
        version = self.assets.warm(page_filter)
        hashed = self.assets.stats["hashed"]
        self.assertEqual(hashed, 11)                      # the page and the ten files it names (directly or not)
        served = self.assets.serve("index.html", None, False)
        self.assertEqual(served.version, version)
        self.assertIn('data-kind="x"', served.body.decode("utf-8"))
        self.assertEqual(self.assets.stats["hashed"], hashed, "the first request after warming hashes nothing")
        self.assets.set_page_filter(page_filter)          # the same filter again (as every request does): nothing dropped
        self.assets.serve("index.html", None, False)
        self.assertEqual(self.assets.stats["hashed"], hashed)

    def test_a_page_request_resolves_paths_only_once(self):
        # Path.resolve() is slow on a busy Windows PC; unchanged files must not be resolved again on every page request.
        self.assets.serve("index.html", None, True)
        calls = []
        original = self.assets._resolve_now
        self.assets._resolve_now = lambda rel: calls.append(rel) or original(rel)
        for _ in range(3):
            self.assertIsNotNone(self.assets.serve("index.html", None, True))
        self.assertEqual(calls, [])

    def test_a_remembered_path_follows_a_changed_or_removed_file(self):
        before = self.body("live.js")
        path = write(self.root, "sub/c.mjs", "export const c = 3;\n")
        touch_later(path)
        self.assertNotEqual(self.body("live.js"), before, "a changed deep module still changes the page's versions")
        self.assertEqual(self.body("sub/c.mjs"), "export const c = 3;\n")
        (self.root / "side.mjs").unlink()
        self.assertIsNone(self.assets.serve("side.mjs", None, False))
        self.assertNotIn("./side.mjs?v=", self.body("live.js"), "a removed file is no longer named with a version")
        write(self.root, "side.mjs", "globalThis.side = 2;\n")
        self.assertEqual(self.body("side.mjs"), "globalThis.side = 2;\n")

    def test_paths_outside_the_root_are_refused(self):
        outside = Path(self.tmp.name).parent / "outside-static-test.txt"
        for rel in ("../outside-static-test.txt", "..", "", "/", "css/../../x", "nothing.js", "css"):
            self.assertIsNone(self.assets.serve(rel, None, False), rel)
        self.assertFalse(outside.exists())

    def test_import_cycle_terminates(self):
        write(self.root, "p.mjs", "import './q.mjs';\n")
        write(self.root, "q.mjs", "import './p.mjs';\n")
        p, q = self.body("p.mjs"), self.body("q.mjs")
        self.assertIn("./q.mjs?v=", p)
        self.assertIsNotNone(self.assets.version("q.mjs"))
        self.assertTrue("./p.mjs" in q)


class RealStaticFolderTest(unittest.TestCase):
    """The page and the module graph of the real static folder."""

    @classmethod
    def setUpClass(cls):
        cls.assets = StaticAssets(STATIC)

    def test_every_static_reference_of_the_page_is_versioned_and_current(self):
        raw = (STATIC / "index.html").read_text(encoding="utf-8")
        page = self.assets.serve("index.html", None, False).body.decode("utf-8")
        self.assertEqual(VERSIONED.sub("", page), raw, "only ?v=... is added to the page")
        refs = re.findall(r'(?:src|href)="/static/([^"?]+)\?v=([0-9a-f]+)"', page)
        plain = re.findall(r'(?:src|href)="/static/([^"?]+)"', page)
        self.assertGreater(len(refs), 25)
        self.assertEqual(plain, [], "every /static/ reference in the page names a version")
        for rel, version in refs:
            self.assertEqual(self.assets.version(rel), version, rel)

    def test_relative_module_imports_are_versioned(self):
        for path in sorted(STATIC.rglob("*")):
            rel = path.relative_to(STATIC).as_posix()
            if path.suffix not in (".mjs", ".js") or (path.suffix == ".js" and rel.startswith("vendor/")):
                continue
            body = self.assets.serve(rel, None, False).body.decode("utf-8")
            for spec in re.findall(r'''(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)["'](\.{1,2}/[^"']+)["']''', body):
                self.assertRegex(spec, VERSIONED, f"{rel}: {spec}")

    def test_font_in_the_style_sheet_is_versioned(self):
        css = self.assets.serve("css/tokens.css", None, False).body.decode("utf-8")
        for name in ("ubts-sans-ui.woff2", "ubts-sans-ko.woff2"):
            version = self.assets.version(f"vendor/ubts-sans/{name}")
            self.assertIn(f"/static/vendor/ubts-sans/{name}?v={version}", css)


try:
    from dashboard_server.app import create_server  # needs Linux (fcntl)
except ImportError:  # pragma: no cover - Windows
    create_server = None


@unittest.skipIf(create_server is None, "the dashboard server imports only on Linux")
class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2)

    def get(self, path, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, b""

    def test_page_and_versioned_file(self):
        status, headers, page = self.get("/rover-control")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], REVALIDATE)
        rel, version = re.search(r'src="/static/(app/02-shell\.js)\?v=([0-9a-f]+)"', page.decode("utf-8")).groups()
        status, headers, body = self.get(f"/static/{rel}?v={version}")
        self.assertEqual((status, headers["Cache-Control"]), (200, LONG_CACHE))
        self.assertEqual(body, (STATIC / rel).read_bytes())
        status, headers, _ = self.get(f"/static/{rel}?v={version}", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)
        status, headers, _ = self.get(f"/static/{rel}")
        self.assertEqual((status, headers["Cache-Control"]), (200, REVALIDATE))
        status, page_headers, _ = self.get("/")
        status, _, _ = self.get("/", {"If-None-Match": page_headers["ETag"]})
        self.assertEqual(status, 304)

    def test_gzip_and_missing(self):
        status, headers, body = self.get("/static/live-map.js", {"Accept-Encoding": "gzip"})
        self.assertEqual((status, headers["Content-Encoding"]), (200, "gzip"))
        self.assertIn(b"./metric-map.mjs?v=", gzip.decompress(body))
        self.assertEqual(self.get("/static/nothing-here.js")[0], 404)
        self.assertEqual(self.get("/static/../dashboard_server/app.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
