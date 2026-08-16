import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.getcwd())

from ezsway.tui.app import _ask


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


if __name__ == "__main__":
    unittest.main()
