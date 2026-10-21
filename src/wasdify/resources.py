"""Additional patch files in the companion ZIP archive."""
from contextlib import contextmanager
from importlib.resources import files
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys
import zipfile
import zlib

from .models import WasdifyError
from .storage import sha256


def safe_relative(value: str) -> tuple[str, ...]:
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (not value or posix.is_absolute() or windows.drive or windows.root
            or "\\" in value or any(p in ("", ".", "..") for p in value.split("/"))
            or any(any(c in '<>:"|?*' or ord(c) < 32 for c in p)
                   or p.endswith((".", " ")) or PureWindowsPath(p).is_reserved()
                   for p in posix.parts)):
        raise WasdifyError("unsafe_resource_path", path=value)
    return posix.parts


def check_path(path: Path) -> None:
    """Reject symlinks and Windows reparse points in every existing component."""
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise WasdifyError("io_error", path=str(part), reason=str(error)) from error
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise WasdifyError("unsafe_resource_path", path=str(part))
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise WasdifyError("unsafe_resource_path", path=str(part))


def destination(executable: Path, relative: str) -> Path:
    path = executable.parent.joinpath(*safe_relative(relative))
    check_path(path)
    return path


@contextmanager
def open_pak():
    try:
        if getattr(sys, "frozen", False):
            source = Path(sys.executable).parent / "WASDify.pak"
            check_path(source)
            content = source.read_bytes()
        else:
            content = files("wasdify").joinpath("data/WASDify.pak").read_bytes()
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set()
            for entry in archive.infolist():
                safe_relative(entry.filename)
                key = entry.filename.casefold()
                if key in names or entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16):
                    raise WasdifyError("invalid_pak")
                names.add(key)
            yield archive
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, zlib.error, NotImplementedError) as error:
        raise WasdifyError("invalid_pak") from error


def read_resource(source: str, digest: str) -> bytes:
    safe_relative(source)
    with open_pak() as archive:
        payload = archive.read(source)
    if sha256(payload) != digest:
        raise WasdifyError("invalid_pak")
    return payload
