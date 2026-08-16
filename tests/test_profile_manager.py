import fcntl
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

sys.path.append(os.getcwd())

from ezsway.core.errors import (
    ConcurrentAccessError,
    InvalidLabelError,
    ProfileLockedError,
    ProfileNotFoundError,
)
from ezsway.core.profile_manager import ProfileManager, validate_label
from ezsway.core.wm_adapter import Monitor, WMAdapter


class FakeWMAdapter(WMAdapter):
    """In-memory WMAdapter for tests -- no real sway/i3ipc involved.

    `apply_is_effective` controls whether enable_output() actually mutates
    the fake monitor's reported state, letting tests exercise the
    "IPC accepted but nothing actually changed" verification path.
    """

    def __init__(self, monitors: List[Monitor], apply_is_effective: bool = True):
        self._monitors = {m.unique_id: m for m in monitors}
        self.apply_is_effective = apply_is_effective
        self.enable_calls = []
        self.disable_calls = []

    def get_outputs(self) -> List[Monitor]:
        return list(self._monitors.values())

    def enable_output(self, monitor_name, mode, position, scale=1.0, transform="normal"):
        self.enable_calls.append((monitor_name, mode, position, scale, transform))
        if not self.apply_is_effective:
            return
        for m in self._monitors.values():
            if m.name == monitor_name:
                w, h = mode.split("x")
                m.width, m.height = int(w), int(h)
                x, y = position.split(" ")
                m.pos_x, m.pos_y = int(x), int(y)
                m.transform = transform
                m.active = True

    def disable_output(self, monitor_name):
        self.disable_calls.append(monitor_name)
        for m in self._monitors.values():
            if m.name == monitor_name:
                m.active = False

    def reload_config(self):
        pass


def make_monitor(name="DP-1", make="Dell", model="M1", serial="S1", active=True):
    return Monitor(
        name=name, make=make, model=model, serial=serial,
        width=1920, height=1080, refresh_rate=60.0,
        scale=1.0, active=active, pos_x=0, pos_y=0,
    )


class TestLabelValidation(unittest.TestCase):
    def test_valid_label_accepted(self):
        self.assertEqual(validate_label("Alissons"), "Alissons")
        self.assertEqual(validate_label("office-2"), "office-2")

    def test_empty_label_rejected(self):
        with self.assertRaises(InvalidLabelError):
            validate_label("")
        with self.assertRaises(InvalidLabelError):
            validate_label("   ")

    def test_path_traversal_rejected(self):
        for bad in ("../etc/passwd", "..", ".", "a/b", "a\\b", "../../x"):
            with self.assertRaises(InvalidLabelError, msg=f"{bad!r} should be rejected"):
                validate_label(bad)

    def test_unsafe_characters_rejected(self):
        for bad in ("a;rm -rf", "a$(whoami)", "a`id`", "a|b"):
            with self.assertRaises(InvalidLabelError, msg=f"{bad!r} should be rejected"):
                validate_label(bad)


class TestProfileManagerBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.wm = FakeWMAdapter([make_monitor()])
        self.pm = ProfileManager(self.wm, config_dir=Path(self.tmpdir))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestSaveLoadRoundTrip(TestProfileManagerBase):
    def test_save_then_list(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        profiles = self.pm.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["label"], "work")
        self.assertFalse(profiles[0]["locked"])

    def test_save_then_load_applies_and_verifies(self):
        m = make_monitor(active=True)
        m.pos_x, m.pos_y = 100, 200
        self.wm = FakeWMAdapter([m])
        self.pm = ProfileManager(self.wm, config_dir=Path(self.tmpdir))
        self.pm.save_profile("work", self.wm.get_outputs())

        # Simulate the monitor moving back to 0,0 (e.g. after a reboot) before load
        m.pos_x, m.pos_y = 0, 0

        result = self.pm.load_profile("work")
        self.assertTrue(result.ok)
        self.assertIn(m.unique_id, result.applied)
        self.assertEqual((m.pos_x, m.pos_y), (100, 200))

    def test_load_marks_ineffective_apply_as_failed(self):
        """The core regression case: WM accepts the command but the output
        never actually changes state -- must be reported as failed, not
        silently treated as success."""
        m = make_monitor()
        m.pos_x, m.pos_y = 100, 200
        self.wm = FakeWMAdapter([m], apply_is_effective=True)
        self.pm = ProfileManager(self.wm, config_dir=Path(self.tmpdir))
        self.pm.save_profile("work", self.wm.get_outputs())  # saves target position 100,200

        # Now make enable_output ineffective AND move the monitor away from the
        # saved target, so a real (as opposed to coincidental) failure to apply
        # is distinguishable from "was already there".
        m.pos_x, m.pos_y = 0, 0
        self.wm.apply_is_effective = False

        result = self.pm.load_profile("work")
        self.assertFalse(result.ok)
        self.assertEqual(result.failed[0]["unique_id"], m.unique_id)

    def test_load_skips_disconnected_monitor_without_failing(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        # New WM state: the saved monitor is gone
        self.pm = ProfileManager(FakeWMAdapter([]), config_dir=Path(self.tmpdir))
        result = self.pm.load_profile("work")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.skipped_not_connected), 1)

    def test_load_nonexistent_profile_raises(self):
        with self.assertRaises(ProfileNotFoundError):
            self.pm.load_profile("does-not-exist")


class TestLocking(TestProfileManagerBase):
    def test_locked_profile_refuses_save_overwrite(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        self.pm.lock_profile("work")
        with self.assertRaises(ProfileLockedError):
            self.pm.save_profile("work", self.wm.get_outputs())

    def test_locked_profile_refuses_remove(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        self.pm.lock_profile("work")
        with self.assertRaises(ProfileLockedError):
            self.pm.remove_profile("work")

    def test_unlock_allows_save_again(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        self.pm.lock_profile("work")
        self.pm.unlock_profile("work")
        self.pm.save_profile("work", self.wm.get_outputs())  # should not raise


class TestRenameRemove(TestProfileManagerBase):
    def test_rename_updates_current_pointer(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        self.pm.load_profile("work")
        self.assertEqual(self.pm.get_current_label(), "work")
        self.pm.rename_profile("work", "office")
        self.assertEqual(self.pm.get_current_label(), "office")
        self.assertEqual(self.pm.list_profiles()[0]["label"], "office")

    def test_rename_to_existing_label_rejected(self):
        self.pm.save_profile("a", self.wm.get_outputs())
        self.pm.save_profile("b", self.wm.get_outputs())
        with self.assertRaises(InvalidLabelError):
            self.pm.rename_profile("a", "b")

    def test_remove_clears_current_pointer(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        self.pm.load_profile("work")
        self.pm.remove_profile("work")
        self.assertIsNone(self.pm.get_current_label())


class TestBackupRestore(TestProfileManagerBase):
    def test_backup_then_restore(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        backup_id = self.pm.backup_profile("work")
        self.assertIn("work__", backup_id)

        # Corrupt the live profile
        (self.pm.profiles_dir / "work.json").write_text("{not valid json")

        restored_label = self.pm.restore_backup(backup_id)
        self.assertEqual(restored_label, "work")
        profiles = self.pm.list_profiles()
        self.assertEqual(profiles[0]["label"], "work")

    def test_restore_nonexistent_backup_raises(self):
        with self.assertRaises(ProfileNotFoundError):
            self.pm.restore_backup("nope__20260101_000000")


class TestCorruptedFileIsolation(TestProfileManagerBase):
    def test_corrupted_profile_isolated_not_dropped_silently(self):
        self.pm.save_profile("good", self.wm.get_outputs())
        bad_path = self.pm.profiles_dir / "bad.json"
        bad_path.write_text("{this is not json")

        profiles = self.pm.list_profiles()
        # Only the good one is listed...
        self.assertEqual([p["label"] for p in profiles], ["good"])
        # ...and the bad one was moved aside, not deleted outright.
        self.assertFalse(bad_path.exists())
        corrupted = list(self.pm.profiles_dir.glob("bad.json.corrupt-*"))
        self.assertEqual(len(corrupted), 1)


class TestAtomicWrite(TestProfileManagerBase):
    def test_no_tmp_file_left_behind_after_successful_save(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        tmp_files = list(self.pm.profiles_dir.glob(".*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_failed_write_does_not_corrupt_existing_profile(self):
        self.pm.save_profile("work", self.wm.get_outputs())
        original_content = (self.pm.profiles_dir / "work.json").read_text()

        # Simulate a write failure partway through by making json.dump blow up
        from unittest.mock import patch as mock_patch
        with mock_patch("ezsway.core.profile_manager.json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.pm.save_profile("work", self.wm.get_outputs())

        # Live file must be untouched -- atomic write means a failed write
        # never replaces the good file with a partial one.
        self.assertEqual((self.pm.profiles_dir / "work.json").read_text(), original_content)
        # And no leftover .tmp file.
        self.assertEqual(list(self.pm.profiles_dir.glob(".*.tmp")), [])


class TestConcurrency(TestProfileManagerBase):
    def test_concurrent_operation_raises_instead_of_blocking(self):
        self.pm.save_profile("work", self.wm.get_outputs())

        # Hold the lock externally, simulating another in-progress operation.
        lock_file = open(self.pm.lock_path, "r+")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(ConcurrentAccessError):
                self.pm.save_profile("work", self.wm.get_outputs())
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()


if __name__ == "__main__":
    unittest.main()
