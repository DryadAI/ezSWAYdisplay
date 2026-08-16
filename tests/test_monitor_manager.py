import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add path
sys.path.append(os.getcwd())

from ezsway.core.monitor_manager import MonitorManager
from ezsway.core.wm_adapter import Monitor
from ezsway.core.errors import ConfigStoreError, MonitorNotFoundError

class TestMonitorManager(unittest.TestCase):
    
    def setUp(self):
        self.mock_wm = MagicMock()
        self.mock_store = MagicMock()
        
        # Patching WMFactory and ConfigStore
        patcher1 = patch('ezsway.core.monitor_manager.WMFactory.create_adapter', return_value=self.mock_wm)
        patcher2 = patch('ezsway.core.monitor_manager.ConfigStore', return_value=self.mock_store)
        
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        
        self.mock_create_adapter = patcher1.start()
        self.mock_ConfigStore = patcher2.start()
        
        self.manager = MonitorManager()
        
    def test_failsafe_fresh_install(self):
        """Test that with no config, at least one monitor is kept active."""
        # Setup: 2 monitors, both unknown, both currently active (default state)
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=True)
        m2 = Monitor("DP-2", "LG", "M2", "456", 1920, 1080, 60.0, active=True)
        
        self.mock_wm.get_outputs.return_value = [m1, m2]
        self.mock_store.is_known.return_value = False # None are known
        self.mock_store.get_monitor_config.return_value = None
        
        self.manager.enforce_policy()
        
        # Expectation: 
        # Fail-safe should keep one active.
        # The other should be disabled.
        # "Fail-safe: Keeping DP-1 active" (since it's first)
        # "Disabling unknown monitor: DP-2"
        
        self.mock_wm.disable_output.assert_called_with("DP-2")
        # DP-1 should NOT be disabled
        call_args_list = self.mock_wm.disable_output.call_args_list
        disabled_names = [args[0][0] for args in call_args_list]
        self.assertNotIn("DP-1", disabled_names)
        self.assertIn("DP-2", disabled_names)

    def test_failsafe_activation_survives_config_store_failure(self):
        """Regression test: if wm.enable_output() succeeds during fail-safe
        activation but config_store.set_monitor_config() then raises
        ConfigStoreError (disk full/permission denied), the monitor that was
        JUST turned on as the safety net must not be immediately disabled
        again by step 3's "disable unknown monitors" loop -- that would
        defeat the entire purpose of fail-safe (ensuring at least one
        display stays on), turning a persistence hiccup into a total
        lockout."""
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=False)
        self.mock_wm.get_outputs.return_value = [m1]
        self.mock_store.is_known.return_value = False
        self.mock_store.set_monitor_config.side_effect = ConfigStoreError("disk full")

        self.manager.enforce_policy()  # must not raise

        self.mock_wm.enable_output.assert_called_once()
        self.mock_wm.disable_output.assert_not_called()
        self.assertTrue(m1.active)

    def test_known_monitor_respected(self):
        """Test that known monitors are respected and unknown ones disabled."""
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=True) # Known
        m2 = Monitor("DP-2", "LG", "M2", "456", 1920, 1080, 60.0, active=True) # Unknown
        
        self.mock_wm.get_outputs.return_value = [m1, m2]
        
        # Mock store behavior
        def is_known_side_effect(uid):
            return uid == m1.unique_id
        self.mock_store.is_known.side_effect = is_known_side_effect
        
        self.manager.enforce_policy()
        
        # Expectation: DP-2 disabled. DP-1 touched (maybe config applied, but definitely not disabled).
        self.mock_wm.disable_output.assert_called_once_with("DP-2")
        
    def test_activate_monitor(self):
        """Test activation logic."""
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=False, pos_x=0, pos_y=0)
        self.manager.monitors = [m1]
        # activate_monitor now verifies the change actually took effect
        # (verify_output_state polls get_outputs() again) -- reflect the
        # post-enable state here, same as a real WM would report.
        self.mock_wm.get_outputs.return_value = [
            Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=True, pos_x=0, pos_y=0)
        ]

        self.manager.activate_monitor(m1.unique_id)

        self.mock_store.set_monitor_config.assert_called()
        # Regression test: activate_monitor must pass the monitor's own detected
        # mode ("1920x1080"), not the literal string "preferred" -- sway has no
        # such mode keyword, so the old value here silently no-op'd on real sway.
        # transform is also required -- omitting it would silently reset a
        # rotated monitor back to "normal" every time it's reactivated.
        self.mock_wm.enable_output.assert_called_with(
            "DP-1", mode="1920x1080", position="0 0", scale=1.0, transform="normal"
        )

    def test_activate_monitor_preserves_transform(self):
        """A rotated monitor being reactivated must keep its rotation, not
        silently reset to "normal"."""
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0,
                      active=False, pos_x=0, pos_y=0, transform="90")
        self.manager.monitors = [m1]
        self.mock_wm.get_outputs.return_value = [
            Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0,
                    active=True, pos_x=0, pos_y=0, transform="90")
        ]

        self.manager.activate_monitor(m1.unique_id)

        self.mock_wm.enable_output.assert_called_with(
            "DP-1", mode="1920x1080", position="0 0", scale=1.0, transform="90"
        )

    def test_deactivate_monitor_refetches_if_stale(self):
        """deactivate_monitor must re-fetch like activate_monitor does, not
        silently no-op if the monitor isn't in the last-known self.monitors list."""
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=True)
        self.manager.monitors = []  # stale/empty -- simulates monitor not yet refreshed
        # First get_outputs() call is the re-fetch that locates the target;
        # the second is verify_output_state()'s post-disable poll -- an
        # empty result there means "no longer connected/active", which
        # correctly counts as verified-disabled.
        self.mock_wm.get_outputs.side_effect = [[m1], []]

        self.manager.deactivate_monitor(m1.unique_id)

        self.mock_wm.disable_output.assert_called_once_with("DP-1")
        self.mock_store.set_monitor_config.assert_called_with(m1.unique_id, {"active": False})

    def test_activate_unknown_monitor_raises_instead_of_silently_returning(self):
        """Regression test: previously logged an error and silently `return`ed,
        contradicting the method's own docstring. A caller wrapping this in
        try/except (as the GUI does) saw no exception at all -- the button
        click just did nothing, with zero feedback."""
        self.mock_wm.get_outputs.return_value = []  # re-fetch also finds nothing
        with self.assertRaises(MonitorNotFoundError):
            self.manager.activate_monitor("does-not-exist")

    def test_deactivate_unknown_monitor_raises_instead_of_silently_returning(self):
        self.mock_wm.get_outputs.return_value = []
        with self.assertRaises(MonitorNotFoundError):
            self.manager.deactivate_monitor("does-not-exist")

    def test_activate_survives_config_store_failure_after_physical_success(self):
        """Regression test: activate_monitor() called enable_output()/
        set_monitor_config() as one unguarded unit, so a persistence-only
        failure (disk full/permission denied) after the physical action
        already succeeded was misreported to the caller as a total failure
        -- potentially prompting a confusing retry that double-toggles
        state that's already correct. enforce_policy()'s fail-safe branch
        was hardened against this exact class in an earlier pass;
        activate_monitor/deactivate_monitor were not."""
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=False)
        self.manager.monitors = [m1]
        self.mock_store.set_monitor_config.side_effect = ConfigStoreError("disk full")
        self.mock_wm.get_outputs.return_value = [
            Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=True, pos_x=0, pos_y=0)
        ]

        self.manager.activate_monitor(m1.unique_id)  # must not raise

        self.mock_wm.enable_output.assert_called_once()

    def test_deactivate_survives_config_store_failure_after_physical_success(self):
        m1 = Monitor("DP-1", "Dell", "M1", "123", 1920, 1080, 60.0, active=True)
        self.manager.monitors = [m1]
        self.mock_store.set_monitor_config.side_effect = ConfigStoreError("disk full")
        # Explicit (not relying on MagicMock's default empty-iterator
        # behavior) -- verify_output_state()'s post-disable poll finding no
        # matching connected monitor counts as verified-disabled.
        self.mock_wm.get_outputs.return_value = []

        self.manager.deactivate_monitor(m1.unique_id)  # must not raise

        self.mock_wm.disable_output.assert_called_once_with("DP-1")

if __name__ == '__main__':
    unittest.main()
