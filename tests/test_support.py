"""Check cleanup on both normal and exceptional test exits."""
from pathlib import Path
import unittest

from tests.support import temporary_directory


class TemporaryDirectoryTests(unittest.TestCase):
    def test_workspace_location_and_cleanup(self):
        with temporary_directory() as directory:
            self.assertEqual(
                directory.parent,
                Path(__file__).resolve().parents[1] / "build" / "test-temp",
            )
            nested = directory / "nested"
            nested.mkdir()
            (nested / "fixture.bin").write_bytes(b"synthetic")
            with temporary_directory() as other:
                self.assertNotEqual(directory, other)
        self.assertFalse(directory.exists())
        self.assertFalse(other.exists())

    def test_cleanup_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with temporary_directory() as directory:
                (directory / "fixture.bin").write_bytes(b"synthetic")
                raise RuntimeError("test failure")
        self.assertFalse(directory.exists())
