"""The shared EXE must dispatch GUI and CLI without altering CLI arguments."""
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.entry_exe import main


class ExeEntryTests(unittest.TestCase):
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
