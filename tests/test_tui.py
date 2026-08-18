import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from ezsway.tui.app import _ask, _edit_display_settings, _manage_monitors


class TestAskEofHandling(unittest.TestCase):
    """Regression test: questionary's .ask() raises a raw EOFError when
    stdin has no controlling terminal (or KeyboardInterrupt on Ctrl+C),
    which previously wasn't caught for several prompts in run_tui() (the
    first-run Setup Wizard confirm, in particular) -- crashing with a
    traceback instead of behaving like any other cancelled prompt (which
    questionary itself represents as a None return)."""

    def test_eof_returns_none(self):
        question = MagicMock()
        question.ask.side_effect = EOFError()
        self.assertIsNone(_ask(question))

    def test_keyboard_interrupt_returns_none(self):
        question = MagicMock()
        question.ask.side_effect = KeyboardInterrupt()
        self.assertIsNone(_ask(question))

    def test_normal_answer_passed_through(self):
        question = MagicMock()
        question.ask.return_value = "some-label"
        self.assertEqual(_ask(question), "some-label")


def _fake_monitor(name="DP-1", make="Dell", model="M1", serial="S1", active=False):
    m = MagicMock()
    m.name, m.make, m.model, m.serial, m.active = name, make, model, serial, active
    m.unique_id = f"{make}-{model}-{serial}"
    return m


class TestManageMonitors(unittest.TestCase):
    """Regression coverage for the TUI's missing counterpart to the GUI's
    per-monitor activate/deactivate buttons -- the default-deny policy has
    always been able to disable a newly-plugged monitor, but until this
    there was no way to turn it back on from the TUI, only the GUI."""

    def setUp(self):
        self.manager = MagicMock()

    def test_no_monitors_shows_error_and_asks_nothing(self):
        self.manager.refresh_monitors.return_value = []
        with patch("ezsway.tui.app._ask") as mock_ask:
            _manage_monitors(self.manager)
        mock_ask.assert_not_called()
        self.manager.activate_monitor.assert_not_called()

    def test_unknown_monitor_offers_activate(self):
        mon = _fake_monitor(active=False)
        self.manager.refresh_monitors.return_value = [mon]
        self.manager.config_store.is_known.return_value = False
        with patch("ezsway.tui.app._ask", side_effect=[
            "DP-1 (Dell M1 S1) - unknown, inactive", True,
        ]):
            _manage_monitors(self.manager)
        self.manager.activate_monitor.assert_called_once_with(mon.unique_id)
        self.manager.deactivate_monitor.assert_not_called()

    def test_known_active_monitor_offers_deactivate(self):
        mon = _fake_monitor(active=True)
        self.manager.refresh_monitors.return_value = [mon]
        self.manager.config_store.is_known.return_value = True
        with patch("ezsway.tui.app._ask", side_effect=[
            "DP-1 (Dell M1 S1) - known, active", True,
        ]):
            _manage_monitors(self.manager)
        self.manager.deactivate_monitor.assert_called_once_with(mon.unique_id)
        self.manager.activate_monitor.assert_not_called()

    def test_cancel_at_selection_takes_no_action(self):
        mon = _fake_monitor()
        self.manager.refresh_monitors.return_value = [mon]
        self.manager.config_store.is_known.return_value = False
        with patch("ezsway.tui.app._ask", side_effect=["(cancel)"]):
            _manage_monitors(self.manager)
        self.manager.activate_monitor.assert_not_called()
        self.manager.deactivate_monitor.assert_not_called()

    def test_declining_confirm_takes_no_action(self):
        mon = _fake_monitor(active=False)
        self.manager.refresh_monitors.return_value = [mon]
        self.manager.config_store.is_known.return_value = False
        with patch("ezsway.tui.app._ask", side_effect=[
            "DP-1 (Dell M1 S1) - unknown, inactive", False,
        ]):
            _manage_monitors(self.manager)
        self.manager.activate_monitor.assert_not_called()


class TestEditDisplaySettings(unittest.TestCase):
    """Regression coverage for the previously-missing way to change a
    monitor's scale or rotation -- the arrange screens (GUI drag canvas,
    TUI arrow-key screen) only ever exposed position; scale/transform were
    only changeable by hand-editing a profile JSON or raw swaymsg."""

    def setUp(self):
        self.wm = MagicMock()
        self.mon = _fake_monitor(name="DP-1", active=True)
        self.mon.scale = 1.0
        self.mon.transform = "normal"
        self.mon.width, self.mon.height = 1920, 1080
        self.mon.pos_x, self.mon.pos_y = 0, 0
        self.wm.get_outputs.return_value = [self.mon]

    def test_no_displays_shows_error_and_asks_nothing(self):
        self.wm.get_outputs.return_value = []
        with patch("ezsway.tui.app._ask") as mock_ask:
            _edit_display_settings(self.wm)
        mock_ask.assert_not_called()
        self.wm.enable_output.assert_not_called()

    def test_full_flow_applies_new_scale_and_transform(self):
        answers = ["DP-1 (Dell M1 S1) - scale 1.0, transform normal", "1.5", "90"]
        with patch("ezsway.tui.app._ask", side_effect=answers), \
             patch("ezsway.tui.app.verify_output_state", return_value=True) as mock_verify:
            _edit_display_settings(self.wm)
        self.wm.enable_output.assert_called_once_with(
            "DP-1", mode="1920x1080", position="0 0", scale=1.5, transform="90")
        mock_verify.assert_called_once()
        self.assertEqual(mock_verify.call_args.kwargs["want_scale"], 1.5)
        self.assertEqual(mock_verify.call_args.kwargs["want_transform"], "90")

    def test_cancel_at_monitor_selection_takes_no_action(self):
        with patch("ezsway.tui.app._ask", side_effect=["(cancel)"]):
            _edit_display_settings(self.wm)
        self.wm.enable_output.assert_not_called()

    def test_invalid_scale_rejected_before_any_wm_call(self):
        answers = ["DP-1 (Dell M1 S1) - scale 1.0, transform normal", "not-a-number"]
        with patch("ezsway.tui.app._ask", side_effect=answers):
            _edit_display_settings(self.wm)
        self.wm.enable_output.assert_not_called()

    def test_cancel_at_transform_prompt_takes_no_action(self):
        answers = ["DP-1 (Dell M1 S1) - scale 1.0, transform normal", "1.5", None]
        with patch("ezsway.tui.app._ask", side_effect=answers):
            _edit_display_settings(self.wm)
        self.wm.enable_output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
