import fcntl
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())

from ezsway.core.config_store import ConfigStore
from ezsway.core.errors import ConcurrentAccessError


class TestConfigStoreBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = ConfigStore(config_dir=Path(self.tmpdir))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestBasicPersistence(TestConfigStoreBase):
    def test_set_and_get(self):
        self.store.set_monitor_config("dev-1", {"active": True})
        self.assertEqual(self.store.get_monitor_config("dev-1"), {"active": True})
        self.assertTrue(self.store.is_known("dev-1"))

    def test_persists_across_instances(self):
        self.store.set_monitor_config("dev-1", {"active": True})
        reloaded = ConfigStore(config_dir=Path(self.tmpdir))
        self.assertTrue(reloaded.is_known("dev-1"))

    def test_forget_monitor(self):
        self.store.set_monitor_config("dev-1", {"active": True})
        self.store.forget_monitor("dev-1")
        self.assertFalse(self.store.is_known("dev-1"))


class TestCorruptedFileRecovery(TestConfigStoreBase):
    def test_corrupted_file_moved_aside_not_silently_discarded(self):
        self.store.set_monitor_config("dev-1", {"active": True})
        self.store.config_file.write_text("{not valid json")

        reloaded = ConfigStore(config_dir=Path(self.tmpdir))  # must not raise
        self.assertFalse(reloaded.is_known("dev-1"))  # corrupted -> fresh start

        corrupted = list(Path(self.tmpdir).glob("monitors.json.corrupt-*"))
        self.assertEqual(len(corrupted), 1)


class TestConcurrencySafety(TestConfigStoreBase):
    def test_concurrent_set_raises_instead_of_corrupting(self):
        self.store.set_monitor_config("dev-1", {"active": True})

        lock_file = open(self.store.lock_path, "r+")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(ConcurrentAccessError):
                self.store.set_monitor_config("dev-2", {"active": True})
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

    def test_two_instances_do_not_clobber_each_others_writes(self):
        """Regression test for the actual race this locking fix closes:
        ConfigStore previously had no locking around its load-mutate-save
        cycle, unlike ProfileManager (added in the same PR) which was
        hardened for the identical problem. Two processes (e.g. the GUI's
        background poller and a cron-invoked `ezswaydisplay enforce`) each
        hold their own in-memory monitors_db snapshot loaded once at
        __init__. Without a fresh re-read under the lock before mutating,
        the second writer's stale snapshot would silently overwrite the
        first writer's already-saved change when it saves its own -- a
        known monitor reverting to "unknown" with no error, undermining the
        default-deny safety invariant this tool exists to enforce."""
        store_a = ConfigStore(config_dir=Path(self.tmpdir))
        store_b = ConfigStore(config_dir=Path(self.tmpdir))
        # Both start from the same (empty) on-disk state.

        store_a.set_monitor_config("dev-A", {"active": True})
        # store_b's in-memory monitors_db is still the stale empty snapshot
        # from __init__ -- but set_monitor_config must re-read fresh under
        # the lock before writing, so dev-A's entry must survive.
        store_b.set_monitor_config("dev-B", {"active": True})

        final = ConfigStore(config_dir=Path(self.tmpdir))
        self.assertTrue(final.is_known("dev-A"), "store_b's write clobbered store_a's change")
        self.assertTrue(final.is_known("dev-B"))


if __name__ == "__main__":
    unittest.main()
