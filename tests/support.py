"""Temporary workspaces for synthetic test data only."""
from contextlib import contextmanager
from pathlib import Path
import shutil
from uuid import uuid4


@contextmanager
def temporary_directory():
    # Keep tests independent of TEMP/TMP and tempfile's cwd fallback. On Windows,
    # Python 3.13+ mkdir(mode=0o700) can also lock out a sandbox's restricted token.
    # Default mkdir permissions inherit the workspace ACL instead. These folders
    # contain only synthetic fixtures, never private game files.
    parent = Path(__file__).resolve().parents[1] / "build" / "test-temp"
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / uuid4().hex
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
