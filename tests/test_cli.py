from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wasdify.cli import main
from wasdify.models import Issue, exit_code
from tests.test_core import SOURCE, definition, game, service_for
from dataclasses import replace


class CLITests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_games_json(self):
        code, out, err = self.invoke(["games", "--lang", "ru", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["games"][0]["id"], "dungeons-of-kremlin")
        self.assertEqual(err, "")

    def test_globals_before_and_after_subcommand(self):
        first = self.invoke(["--lang", "ru", "--json", "games"])
        second = self.invoke(["games", "--lang=ru", "--json"])
        self.assertEqual(first, second)

    def test_help_in_both_languages(self):
        for language, word in (("ru", "Проверить"), ("en", "Validate")):
            with redirect_stdout(StringIO()) as stream, self.assertRaises(SystemExit) as caught:
                main(["--lang", language, "--help"])
            self.assertEqual(caught.exception.code, 0)
            self.assertIn(word, stream.getvalue())

    def test_usage_error_json_and_localized_text(self):
        code, out, err = self.invoke(["--json", "apply"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["issues"][0]["code"], "invalid_arguments")
        code, out, err = self.invoke(["--lang", "ru", "unknown"])
        self.assertIn("Некорректные аргументы", err)

    def test_io_error_json(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, err = self.invoke(["detect", str(Path(folder) / "missing"), "--json"])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(out)["issues"][0]["code"], "io_error")

    def test_unknown_file_and_unsupported_render_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "unknown.exe"
            target.write_bytes(b"unknown")
            self.assertEqual(self.invoke(["detect", str(target), "--json"])[0], 3)
            code, out, err = self.invoke(["apply", str(target), "--game", "dungeons-of-kremlin", "--patch", "render-optimization", "--json"])
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(out)["plan"]["issues"][0]["code"], "unsupported_executable")
            self.assertEqual(target.read_bytes(), b"unknown")
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_successful_synthetic_cli_apply_and_hex_serialization(self):
        with tempfile.TemporaryDirectory() as folder, patch("wasdify.cli.Service", return_value=service_for()):
            target = Path(folder) / "test.exe"
            target.write_bytes(SOURCE)
            args = [str(target), "--patch", "first", "--json"]
            code, out, _ = self.invoke(["plan", *args])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["plan"]["changes"][0]["expected"], b"ABC".hex())
            code, out, _ = self.invoke(["apply", *args, "--no-backup"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["result"]["status"], "applied")

    def test_all_issue_exit_categories(self):
        for code, expected in (("signature_count", 3), ("not_implemented", 5), ("backup_failed", 4)):
            self.assertEqual(exit_code((Issue(code),)), expected)
        self.assertEqual(exit_code(()), 0)


if __name__ == "__main__":
    unittest.main()

