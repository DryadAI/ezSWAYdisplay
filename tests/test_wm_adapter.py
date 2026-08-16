import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.append(os.getcwd())

from ezsway.core.wm_adapter import SwayAdapter, WMFactory, HyprlandAdapter
from ezsway.core.errors import WMCommandError


class TestSwayAdapterCommandChecking(unittest.TestCase):
    """Regression tests for the previously-silent IPC-reply-ignoring bug:
    SwayAdapter._run_command() used to fire i3ipc commands and never check
    the reply's success/error fields, so a rejected command (bad mode, bad
    transform, monitor gone) failed completely silently."""

    def setUp(self):
        with patch("ezsway.core.wm_adapter.i3ipc.Connection"):
            self.adapter = SwayAdapter()
        self.adapter.ipc = MagicMock()

    def test_successful_command_does_not_raise(self):
        reply = MagicMock(success=True, error=None)
        self.adapter.ipc.command.return_value = [reply]
        self.adapter.enable_output("DP-1", mode="1920x1080", position="0 0")  # should not raise

    def test_rejected_command_raises_wmcommanderror_with_message(self):
        reply = MagicMock(success=False, error="Unknown output DP-1")
        self.adapter.ipc.command.return_value = [reply]
        with self.assertRaises(WMCommandError) as ctx:
            self.adapter.enable_output("DP-1", mode="1920x1080", position="0 0")
        self.assertIn("Unknown output DP-1", str(ctx.exception))

    def test_invalid_transform_rejected_before_reaching_wm(self):
        with self.assertRaises(ValueError):
            self.adapter.enable_output("DP-1", mode="1920x1080", position="0 0", transform="sideways")
        self.adapter.ipc.command.assert_not_called()

    def test_valid_transform_included_in_command(self):
        reply = MagicMock(success=True, error=None)
        self.adapter.ipc.command.return_value = [reply]
        self.adapter.enable_output("DP-1", mode="1920x1080", position="0 0", transform="90")
        sent_command = self.adapter.ipc.command.call_args[0][0]
        self.assertIn("transform 90", sent_command)

    def test_ipc_exception_wrapped_as_wmcommanderror(self):
        self.adapter.ipc.command.side_effect = RuntimeError("socket closed")
        with self.assertRaises(WMCommandError):
            self.adapter.disable_output("DP-1")


class TestWMFactoryHyprland(unittest.TestCase):
    """Regression test: previously a detected Hyprland session silently got a
    stub adapter whose methods all no-op'd (empty monitor list, no error).
    It must now raise clearly instead."""

    def test_hyprland_detected_raises_not_implemented(self):
        with patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc123"}, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SWAYSOCK", None)
                with self.assertRaises(NotImplementedError):
                    WMFactory.create_adapter()

    def test_hyprland_adapter_methods_raise_not_implemented(self):
        adapter = HyprlandAdapter()
        with self.assertRaises(NotImplementedError):
            adapter.get_outputs()
        with self.assertRaises(NotImplementedError):
            adapter.enable_output("m1", "1920x1080", "0 0")
        with self.assertRaises(NotImplementedError):
            adapter.disable_output("m1")


if __name__ == "__main__":
    unittest.main()
