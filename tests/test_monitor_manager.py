import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add path
sys.path.append(os.getcwd())

from ezsway.core.monitor_manager import MonitorManager
from ezsway.core.wm_adapter import Monitor

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
        
        self.manager.activate_monitor(m1.unique_id)
        
        self.mock_store.set_monitor_config.assert_called()
        self.mock_wm.enable_output.assert_called_with("DP-1", mode="preferred", position="0 0", scale=1.0)

if __name__ == '__main__':
    unittest.main()
