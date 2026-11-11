"""ui_metrics의 순수 함수 시험. 브라우저 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui_metrics  # noqa: E402


def box(x, y, w, h, **extra):
    return {"x": x, "y": y, "w": w, "h": h, "sel": extra.pop("sel", "el"), "ancestors": [], "label": None, "layer": "flow", **extra}


class IntersectionTest(unittest.TestCase):
    def test_overlapping_boxes(self):
        self.assertEqual(ui_metrics.intersection(box(0, 0, 100, 50), box(60, 20, 100, 50)), (40, 30))

    def test_touching_edges_do_not_overlap(self):
        width, height = ui_metrics.intersection(box(0, 0, 100, 50), box(100, 0, 100, 50))
        self.assertEqual(width, 0)
        self.assertFalse(ui_metrics.boxes_overlap(box(0, 0, 100, 50), box(100, 0, 100, 50)))

    def test_separated_boxes_give_negative_size(self):
        width, height = ui_metrics.intersection(box(0, 0, 10, 10), box(50, 50, 10, 10))
        self.assertLess(width, 0)
        self.assertLess(height, 0)

    def test_sliver_below_threshold_is_ignored(self):
        self.assertFalse(ui_metrics.boxes_overlap(box(0, 0, 100, 50), box(98.5, 0, 100, 50)))
        self.assertTrue(ui_metrics.boxes_overlap(box(0, 0, 100, 50), box(97, 0, 100, 50)))

    def test_contained_box_overlaps(self):
        self.assertTrue(ui_metrics.boxes_overlap(box(0, 0, 100, 100), box(10, 10, 20, 20)))


class OverlapTest(unittest.TestCase):
    def test_reports_overlapping_pair(self):
        result = ui_metrics.find_overlaps([box(0, 0, 100, 40, sel="a"), box(50, 10, 100, 40, sel="b"), box(300, 0, 50, 40, sel="c")])
        self.assertEqual(len(result["same_layer"]), 1)
        self.assertEqual((result["same_layer"][0]["a"], result["same_layer"][0]["b"]), ("a", "b"))
        self.assertEqual((result["same_layer"][0]["w"], result["same_layer"][0]["h"]), (50, 30))

    def test_parent_and_child_are_skipped(self):
        parent = box(0, 0, 200, 60, sel="row")
        child = box(10, 10, 80, 40, sel="button", ancestors=[0])
        self.assertEqual(ui_metrics.find_overlaps([parent, child])["same_layer"], [])

    def test_same_label_is_skipped(self):
        first = box(0, 0, 20, 20, label=3)
        second = box(5, 5, 20, 20, label=3)
        other = box(5, 5, 20, 20, label=4)
        self.assertEqual(ui_metrics.find_overlaps([first, second])["same_layer"], [])
        self.assertEqual(len(ui_metrics.find_overlaps([first, other])["same_layer"]), 1)

    def test_pinned_bar_over_flow_content_is_listed_separately(self):
        bar = box(0, 780, 390, 64, sel="bottom-bar", layer="fixed")
        content = box(20, 800, 100, 40, sel="button")
        result = ui_metrics.find_overlaps([bar, content])
        self.assertEqual(result["same_layer"], [])
        self.assertEqual(len(result["cross_layer"]), 1)

    def test_two_pinned_elements_count_as_overlap(self):
        result = ui_metrics.find_overlaps([box(0, 0, 100, 40, layer="fixed"), box(50, 0, 100, 40, layer="sticky")])
        self.assertEqual(len(result["same_layer"]), 1)


class SmallTargetTest(unittest.TestCase):
    def test_small_width_or_height_counts(self):
        elements = [box(0, 0, 92, 34, sel="stop"), box(0, 0, 44, 44, sel="ok"), box(0, 0, 30, 60, sel="narrow")]
        result = ui_metrics.small_targets(elements)
        self.assertEqual([row["sel"] for row in result["small"]], ["stop", "narrow"])

    def test_label_hit_area_is_used(self):
        checkbox = box(0, 0, 13, 13, sel="checkbox", hit={"x": 0, "y": 0, "w": 200, "h": 48}, hit_source="label")
        self.assertEqual(ui_metrics.small_targets([checkbox])["small"], [])

    def test_inline_text_links_are_listed_separately(self):
        link = box(0, 0, 60, 18, sel="a", inline_text_link=True)
        result = ui_metrics.small_targets([link])
        self.assertEqual(result["small"], [])
        self.assertEqual(len(result["inline_links"]), 1)


class CanvasTest(unittest.TestCase):
    def test_matching_buffer(self):
        self.assertIsNone(ui_metrics.canvas_mismatch({"css_w": 345.3, "css_h": 345.3, "buffer_w": 345, "buffer_h": 345, "dpr": 1}))

    def test_stale_buffer(self):
        row = ui_metrics.canvas_mismatch({"sel": "canvas#lidar", "css_w": 345, "css_h": 345, "buffer_w": 324, "buffer_h": 324, "dpr": 1})
        self.assertEqual((row["delta_w"], row["delta_h"]), (-21.0, -21.0))

    def test_device_pixel_ratio_is_divided_out(self):
        self.assertIsNone(ui_metrics.canvas_mismatch({"css_w": 300, "css_h": 150, "buffer_w": 450, "buffer_h": 225, "dpr": 1.5}))
        self.assertIsNotNone(ui_metrics.canvas_mismatch({"css_w": 300, "css_h": 150, "buffer_w": 300, "buffer_h": 150, "dpr": 1.5}))

    def test_two_pixel_tolerance(self):
        self.assertIsNone(ui_metrics.canvas_mismatch({"css_w": 100, "css_h": 100, "buffer_w": 102, "buffer_h": 98, "dpr": 1}))
        self.assertIsNotNone(ui_metrics.canvas_mismatch({"css_w": 100, "css_h": 100, "buffer_w": 103, "buffer_h": 100, "dpr": 1}))

    def test_resize_rows_mark_canvas_that_did_not_follow(self):
        before = [
            {"index": 0, "id": "chart", "cls": "", "visible": True, "css_w": 324, "css_h": 190, "buffer_w": 324, "buffer_h": 190, "dpr": 1},
            {"index": 1, "id": "map", "cls": "", "visible": True, "css_w": 324, "css_h": 300, "buffer_w": 324, "buffer_h": 300, "dpr": 1},
            {"index": 2, "id": "fixed", "cls": "", "visible": True, "css_w": 200, "css_h": 200, "buffer_w": 100, "buffer_h": 100, "dpr": 1},
        ]
        after = [
            {"index": 0, "id": "chart", "cls": "", "visible": True, "css_w": 352, "css_h": 210, "buffer_w": 324, "buffer_h": 190, "dpr": 1},
            {"index": 1, "id": "map", "cls": "", "visible": True, "css_w": 352, "css_h": 300, "buffer_w": 352, "buffer_h": 300, "dpr": 1},
            {"index": 2, "id": "fixed", "cls": "", "visible": True, "css_w": 200, "css_h": 200, "buffer_w": 100, "buffer_h": 100, "dpr": 1},
        ]
        rows = {row["sel"]: row for row in ui_metrics.canvas_resize_rows(before, after)}
        self.assertTrue(rows["canvas#chart"]["stale"])
        self.assertFalse(rows["canvas#map"]["stale"])
        # 표시 크기가 바뀌지 않은 캔버스는 이 검사로 판단하지 않는다(정지 상태 불일치는 다른 수치가 센다).
        self.assertFalse(rows["canvas#fixed"]["css_changed"])
        self.assertFalse(rows["canvas#fixed"]["stale"])


class StopTest(unittest.TestCase):
    def raw(self, **overrides):
        candidate = {"selector": "#roverStopButton", "sel": "button#roverStopButton", "visible": True, "x": 1313, "y": 592, "w": 92, "h": 34, "layer": "flow", "hit_test": True}
        candidate.update(overrides)
        return {"candidates": [candidate], "primary_index": 0 if candidate["visible"] else -1}

    def test_inside_first_viewport(self):
        summary = ui_metrics.stop_summary(self.raw(), [{"in_viewport": True}, {"in_viewport": False}], 1440, 900)
        self.assertTrue(summary["in_first_viewport"])
        self.assertFalse(summary["always_in_viewport"])
        self.assertEqual(summary["top_px"], 592)

    def test_far_below_the_fold(self):
        summary = ui_metrics.stop_summary(self.raw(x=280, y=5134), [{"in_viewport": False}], 390, 844)
        self.assertTrue(summary["visible"])
        self.assertFalse(summary["in_first_viewport"])

    def test_cut_off_at_the_right_edge_is_not_inside(self):
        summary = ui_metrics.stop_summary(self.raw(x=350, y=100), [{"in_viewport": False}], 390, 844)
        self.assertFalse(summary["in_first_viewport"])

    def test_pinned_stop_is_always_in_viewport(self):
        summary = ui_metrics.stop_summary(self.raw(x=280, y=8, layer="fixed"), [{"in_viewport": True}] * 5, 390, 844)
        self.assertTrue(summary["in_first_viewport"])
        self.assertTrue(summary["always_in_viewport"])

    def test_hidden_or_missing(self):
        hidden = ui_metrics.stop_summary(self.raw(visible=False), [], 390, 844)
        self.assertTrue(hidden["found"])
        self.assertFalse(hidden["visible"])
        self.assertIsNone(hidden["top_px"])
        missing = ui_metrics.stop_summary({"candidates": [], "primary_index": -1}, [], 390, 844)
        self.assertFalse(missing["found"])
        self.assertFalse(missing["in_first_viewport"])


class PageMetricsTest(unittest.TestCase):
    def test_metrics_from_raw_measurement(self):
        raw = {
            "document": {"scroll_width": 411, "client_width": 390, "scroll_height": 9720, "body_scroll_width": 411},
            "effective_viewport": {"width": 390, "height": 844},
            "outside_viewport": {"count": 3, "count_all": 9, "items": []},
            "content_overflow_x": {"count": 1, "items": []},
            "content_overflow_y": {"count": 0, "items": []},
            "clipped": {"count": 2, "items": []},
            "ellipsis": {"count": 0, "items": []},
            "interactive": {"items": [box(0, 0, 92, 34), box(0, 100, 120, 48, in_first_viewport=True)], "skipped": {}},
            "canvases": [{"sel": "canvas#a", "css_w": 345, "css_h": 345, "buffer_w": 324, "buffer_h": 324, "dpr": 1}],
            "images": {"total": 1, "broken": [{"sel": "img#camera"}]},
            "stop": {"candidates": [{"selector": "#stop", "visible": True, "x": 280, "y": 5134, "w": 92, "h": 34}], "primary_index": 0},
            "style": {"font_sizes": {"12px": 3, "14px": 5}, "font_weights": {"400": 2}, "text_elements": 8, "heavy_text_elements": 4, "gradient_elements": 11},
            "probe_problems": [],
        }
        events = {"console_errors": ["x"], "page_errors": [], "failed_requests": [], "http_errors": [{"status": 404, "url": "/x"}]}
        metrics, details = ui_metrics.page_metrics(raw, events, {"name": "390x844", "width": 390, "height": 844}, [{"in_viewport": False}])
        self.assertEqual(metrics["horizontal_overflow_px"], 21)
        self.assertEqual(metrics["overflowing_elements"], 1)
        self.assertEqual(metrics["clipped_texts"], 2)
        self.assertEqual(metrics["overlaps"], 0)
        self.assertEqual(metrics["small_targets"], 1)
        self.assertEqual(metrics["interactive_total"], 2)
        self.assertEqual(metrics["canvas_mismatches"], 1)
        self.assertIsNone(metrics["canvas_stale_after_resize"])
        self.assertEqual(metrics["stop_top_px"], 5134)
        self.assertFalse(metrics["stop_in_first_viewport"])
        self.assertEqual(metrics["page_height_px"], 9720)
        self.assertEqual(metrics["screens"], 11.52)
        self.assertEqual(metrics["console_errors"], 1)
        self.assertEqual(metrics["http_errors"], 1)
        self.assertEqual(metrics["broken_images"], 1)
        self.assertEqual(metrics["style_font_size_kinds"], 2)
        self.assertEqual(metrics["style_heavy_text_percent"], 50.0)
        self.assertEqual(details["canvas_mismatches"][0]["sel"], "canvas#a")

    def test_keys_and_slugs(self):
        self.assertEqual(ui_metrics.page_key("stress", "/rover-control", "390x844"), "stress|/rover-control|390x844")
        self.assertEqual(ui_metrics.route_slug("/"), "overview")
        self.assertEqual(ui_metrics.route_slug("/rover-control"), "rover-control")
        self.assertEqual(ui_metrics.route_slug("/docs/a"), "docs_a")


if __name__ == "__main__":
    unittest.main()
