"""The shared EXE must dispatch GUI and CLI without altering CLI arguments."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.entry_exe import main


class ExeEntryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows windowed interpreter required")
    def test_windowed_cli_restores_redirected_unicode_output(self):
        completed = subprocess.run(
            [str(Path(sys.executable).with_name("pythonw.exe")), "-c",
             "import sys; from scripts.entry_exe import _restore_cli_streams; "
             "sys.stdout = sys.stderr = None; "
             "sys.frozen = True; _restore_cli_streams(); "
             "print('WASDify \\u041a\\u0440\\u0435\\u043c\\u043b\\u044c'); "
             "print('diagnostic', file=sys.stderr); raise SystemExit(3)"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(completed.stdout.strip(), "WASDify \u041a\u0440\u0435\u043c\u043b\u044c")
        self.assertEqual(completed.stderr.strip(), "diagnostic")

    def test_no_arguments_opens_gui(self):
        with patch("scripts.entry_exe.cli_main", return_value=4) as cli:
            self.assertEqual(main([]), 4)
            cli.assert_called_once_with(["gui"])

    def test_commands_and_options_keep_cli_semantics(self):
        for args in (["games", "--json"], ["--lang", "ru", "gui"],
                     ["gui", "--help"], ["--help"], ["--version"], ["unknown"]):
            with self.subTest(args=args), patch("scripts.entry_exe.cli_main", return_value=2) as cli:
                self.assertEqual(main(args), 2)
                cli.assert_called_once_with(args)

    def test_process_arguments(self):
        with patch.object(sys, "argv", ["WASDify.exe", "--json", "games"]), \
                patch("scripts.entry_exe.cli_main", return_value=0) as cli:
            self.assertEqual(main(), 0)
            cli.assert_called_once_with(["--json", "games"])

    def test_cli_does_not_import_tkinter(self):
        completed = subprocess.run(
            [sys.executable, "-c",
             "import sys; from scripts.entry_exe import main; "
             "assert main(['games', '--json']) == 0; "
             "assert 'tkinter' not in sys.modules"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn('"games"', completed.stdout)
