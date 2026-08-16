import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.getcwd())

from ezsway.core.errors import WMCommandError, WMNotReachableError
from ezsway.core.profile_manager import ProfileManager
from ezsway.core.setup_wizard import SetupWizard, default_fingerprint_label
from ezsway.core.wm_adapter import Monitor

from tests.test_profile_manager import FakeWMAdapter, make_monitor


class TestDefaultFingerprint(unittest.TestCase):
    def test_fingerprint_is_sorted_and_stable(self):
        m1 = make_monitor(name="DP-1", make="Dell", model="A")
        m2 = make_monitor(name="DP-2", make="LG", model="B")
        self.assertEqual(default_fingerprint_label([m2, m1]), default_fingerprint_label([m1, m2]))

    def test_fingerprint_ignores_inactive_monitors(self):
        m1 = make_monitor(name="DP-1", make="Dell", model="A", active=True)
        m2 = make_monitor(name="DP-2", make="LG", model="B", active=False)
        fp = default_fingerprint_label([m1, m2])
        self.assertIn("Dell", fp)
        self.assertNotIn("LG", fp)

    def test_empty_monitor_list_falls_back(self):
        self.assertEqual(default_fingerprint_label([]), "default")


class TestSetupWizard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.monitor = make_monitor()
        self.wm = FakeWMAdapter([self.monitor])
        self.pm = ProfileManager(self.wm, config_dir=Path(self.tmpdir))
        self.wizard = SetupWizard(self.wm, self.pm)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_is_first_run_true_initially(self):
        self.assertTrue(self.wizard.is_first_run())

    def test_run_creates_profile_and_clears_first_run(self):
        label = self.wizard.run()
        self.assertFalse(self.wizard.is_first_run())
        self.assertEqual([p["label"] for p in self.pm.list_profiles()], [label])

    def test_run_with_explicit_label(self):
        label = self.wizard.run(label="my-setup")
        self.assertEqual(label, "my-setup")

    def test_rerun_backs_up_previous_profile(self):
        self.wizard.run(label="work")
        self.wizard.run(label="work")  # second run over the same label
        backups = self.pm.list_backups("work")
        self.assertEqual(len(backups), 1)

    def test_wm_unreachable_raises_clear_error(self):
        class BrokenWMAdapter(FakeWMAdapter):
            def get_outputs(self):
                raise WMCommandError("Cannot query sway outputs (is sway running?): no socket")

        broken_wm = BrokenWMAdapter([])
        wizard = SetupWizard(broken_wm, self.pm)
        with self.assertRaises(WMNotReachableError):
            wizard.run()

    def test_no_monitors_raises_clear_error(self):
        empty_wm = FakeWMAdapter([])
        wizard = SetupWizard(empty_wm, self.pm)
        with self.assertRaises(WMNotReachableError):
            wizard.run()


if __name__ == "__main__":
    unittest.main()
