"""Tk integration tests: real widgets, synthetic game bytes only."""
from tests.support import temporary_directory
import time
from pathlib import Path
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
    from wasdify.gui import Application
except ImportError:
    tk = None

from wasdify.models import Issue, Plan
from tests.test_core import SOURCE, definition, game, service_for
from dataclasses import replace


@unittest.skipIf(tk is None, "Tkinter is not installed")
class GUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.directory = self.enterContext(temporary_directory())
        self.path = Path(self.directory) / "renamed.exe"
        self.path.write_bytes(SOURCE)
        self.app = Application(self.root, service_for(), language="en")
        self.root.update()

    def finish(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertFalse(self.app.busy, "GUI worker did not finish")
        self.root.update()

    def select_and_check(self):
        self.app.path.set(str(self.path))
        self.app.check()
        self.finish()
        self.app.patch_vars["first"].set(True)
        self.app._invalidate()
        self.app.check()
        self.finish()

    def test_dos_setup_language_follows_game_not_gui(self):
        from tests.test_kremlin_settings import executable
        from wasdify.service import Service
        self.path.write_bytes(executable('en'))
        self.app = Application(self.root, Service(), language='ru')
        self.app.path.set(str(self.path))
        self.app.check()
        self.finish()
        self.assertEqual(self.app.tr.language,'ru')
        for key in ('wasd','resolution-640x400','game-settings'):
            self.app.patch_vars[key].set(True)
        self.app.check()
        self.finish()
        self.assertIsNotNone(self.app.plan)
        self.app.apply()
        self.finish()
        self.assertIn(b'Dungeons of Kremlin setup',(Path(self.directory)/'SETUP.EXE').read_bytes())

    def test_browse_detects_then_selection_can_apply(self):
        with patch("wasdify.gui.filedialog.askopenfilename", return_value=str(self.path)):
            self.app.browse()
        self.finish()
        self.assertEqual(self.app.detected_game_id, "test-game")
        self.assertFalse(self.app.patch_vars["first"].get())
        self.assertTrue(self.app.backup.get())
        self.assertEqual(str(self.app.apply_button["state"]), "disabled")
        self.app.patch_vars["first"].set(True)
        self.app.check()
        self.finish()
        self.assertEqual(str(self.app.apply_button["state"]), "normal")
        self.app.apply()
        self.finish()
        self.assertEqual(self.path.read_bytes(), SOURCE.replace(b"ABC", b"XYZ"))
        self.assertEqual(len(list(self.path.parent.glob("*.bak"))), 1)

    def test_placeholder_cannot_be_applied(self):
        self.app.service = service_for(game(replace(definition(), implemented=False)))
        self.select_and_check()
        self.assertIsInstance(self.app.last_result, Plan)
        self.assertEqual(self.app.last_result.issues[0].code, "not_implemented")
        self.assertEqual(str(self.app.apply_button["state"]), "disabled")
        self.app.apply()
        self.assertEqual(self.path.read_bytes(), SOURCE)
        self.assertFalse(list(self.path.parent.glob("*.bak")))

    def test_language_preserves_valid_plan_and_selection(self):
        self.select_and_check()
        plan = self.app.plan
        self.app.language.set("ru")
        self.app._switch_language()
        self.assertIs(self.app.plan, plan)
        self.assertTrue(self.app.patch_vars["first"].get())
        self.assertEqual(self.app.check_button["text"], "Проверить")
        self.assertIn("Можно применять", self.app.result_widget.get("1.0", "end"))

    def test_path_and_selection_invalidate_plan(self):
        self.select_and_check()
        self.app.path.set(str(self.path) + "other")
        self.assertIsNone(self.app.plan)
        self.assertEqual(str(self.app.apply_button["state"]), "disabled")

    def test_manual_unknown_file_still_validates_signatures(self):
        self.path.write_bytes(b"unknown")
        self.app.game_box.current(1)
        self.app._select_game()
        self.app.path.set(str(self.path))
        self.app.patch_vars["first"].set(True)
        self.app.check()
        self.finish()
        self.assertEqual(self.app.last_result.issues[0].code, "signature_count")

    def test_main_loop_stays_responsive_and_worker_errors_display(self):
        def slow_detection(*args):
            time.sleep(0.12)
            raise RuntimeError("synthetic failure")

        pulses = []

        def pulse():
            pulses.append(1)
            if self.app.busy:
                self.root.after(10, pulse)

        self.app.path.set(str(self.path))
        with patch.object(self.app.service, "detect", side_effect=slow_detection):
            self.app.check()
            self.root.after(10, pulse)
            self.finish()
        self.assertGreater(len(pulses), 2)
        self.assertEqual(self.app.last_result, Issue("unexpected_error"))

    def test_keyboard_can_focus_check_control(self):
        self.root.deiconify()
        self.root.update()
        self.app.path_entry.focus_force()
        self.root.update()
        self.app.path_entry.event_generate("<Tab>")
        self.root.update()
        self.assertIsNotNone(self.root.focus_get())
        self.assertNotEqual(self.root.focus_get(), self.app.path_entry)


if __name__ == "__main__":
    unittest.main()

