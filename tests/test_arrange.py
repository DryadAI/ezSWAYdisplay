import os
import sys
import unittest

sys.path.append(os.getcwd())

from ezsway.tui.arrange import apply_positions, compute_snap, _fit_scale

from tests.test_profile_manager import FakeWMAdapter, make_monitor


class TestComputeSnap(unittest.TestCase):
    def test_no_other_monitors_no_snap(self):
        self.assertEqual(compute_snap("a", {"a": (100, 100)}, {"a": (800, 600)}), (0, 0))

    def test_snaps_to_nearby_right_edge(self):
        # "a" is at x=805, "b" (width 800) ends at x=800 -- 5px gap, within threshold.
        positions = {"a": (805, 0), "b": (0, 0)}
        sizes = {"a": (800, 600), "b": (800, 600)}
        dx, dy = compute_snap("a", positions, sizes, threshold=40)
        self.assertEqual(dx, -5)  # moves "a" left by 5 so its left edge meets "b"'s right edge
        self.assertEqual(dy, 0)

    def test_far_apart_no_snap(self):
        positions = {"a": (2000, 0), "b": (0, 0)}
        sizes = {"a": (800, 600), "b": (800, 600)}
        self.assertEqual(compute_snap("a", positions, sizes, threshold=40), (0, 0))

    def test_snaps_on_both_axes_independently(self):
        # "a" is close to "b"'s right edge horizontally AND close to "b"'s
        # bottom edge vertically -- both edges should snap in the same call
        # (touch-edge snapping, same rule as ArrangeCanvas.snap_item --
        # aligning same-side edges, e.g. top-to-top, is not what this does).
        positions = {"a": (808, 605), "b": (0, 0)}
        sizes = {"a": (800, 600), "b": (800, 600)}
        dx, dy = compute_snap("a", positions, sizes, threshold=40)
        self.assertEqual(dx, -8)
        self.assertEqual(dy, -5)


class TestApplyPositions(unittest.TestCase):
    def test_active_monitor_applied_and_verified(self):
        m = make_monitor(name="DP-1", active=True)
        wm = FakeWMAdapter([m])
        monitors = {m.unique_id: m}
        applied, skipped, failed = apply_positions(wm, monitors, {m.unique_id: (100, 200)})
        self.assertEqual(applied, ["DP-1"])
        self.assertEqual(skipped, [])
        self.assertEqual(failed, [])
        self.assertEqual(wm.enable_calls[0][0], "DP-1")

    def test_inactive_monitor_skipped_not_applied(self):
        m = make_monitor(name="DP-1", active=False)
        wm = FakeWMAdapter([m])
        monitors = {m.unique_id: m}
        applied, skipped, failed = apply_positions(wm, monitors, {m.unique_id: (100, 200)})
        self.assertEqual(skipped, ["DP-1"])
        self.assertEqual(applied, [])
        self.assertEqual(wm.enable_calls, [])

    def test_verification_failure_reported_as_failed(self):
        """apply_is_effective=False simulates 'IPC said success but nothing
        actually moved' -- apply_positions must not report it as applied."""
        m = make_monitor(name="DP-1", active=True)
        wm = FakeWMAdapter([m], apply_is_effective=False)
        monitors = {m.unique_id: m}
        applied, skipped, failed = apply_positions(wm, monitors, {m.unique_id: (100, 200)})
        self.assertEqual(failed, ["DP-1"])
        self.assertEqual(applied, [])


class TestFitScale(unittest.TestCase):
    def test_empty_positions_returns_default(self):
        self.assertGreater(_fit_scale({}, {}, 80, 24), 0)

    def test_scale_fits_within_available_space(self):
        positions = {"a": (0, 0), "b": (3440, 0)}
        sizes = {"a": (1920, 1080), "b": (1920, 1080)}
        scale = _fit_scale(positions, sizes, 80, 24)
        bbox_w = 3440 + 1920
        self.assertLessEqual(bbox_w * scale, 80)


if __name__ == "__main__":
    unittest.main()
