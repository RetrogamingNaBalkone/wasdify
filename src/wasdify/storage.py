"""Single-file writes: stage, verify, optional exclusive backup, replace."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from uuid import uuid4

from .models import ApplyResult, WasdifyError


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_target(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise WasdifyError("io_error", path=str(path), reason="regular_file_required")
        return path.read_bytes()
    except OSError as error:
        raise WasdifyError("io_error", path=str(path), reason=str(error)) from error


def write_result(path: Path, expected_sha256: str, result: bytes,
                 *, backup: bool = True) -> ApplyResult:
    original = read_target(path)
    if sha256(original) != expected_sha256:
        raise WasdifyError("file_changed", path=str(path))
    result_hash = sha256(result)
    if original == result:
        return ApplyResult("no_changes", expected_sha256, result_hash)
    temporary = None
    backup_path = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=path.name + ".wasdify-", suffix=".tmp",
                delete=False) as staged:
            temporary = Path(staged.name)
            staged.write(result)
            staged.flush()
            os.fsync(staged.fileno())
        if temporary.read_bytes() != result:
            raise WasdifyError("write_failed", path=str(path), reason="verification_failed")
        os.chmod(temporary, mode)
        if backup:
            backup_path = path.with_name(path.name + ".wasdify-" + uuid4().hex + ".bak")
            created = False
            try:
                with backup_path.open("xb") as saved:
                    created = True
                    saved.write(original)
                    saved.flush()
                    os.fsync(saved.fileno())
                if backup_path.read_bytes() != original:
                    raise OSError("Backup verification failed")
            except OSError as error:
                if created:
                    backup_path.unlink(missing_ok=True)
                raise WasdifyError("backup_failed", path=str(backup_path), reason=str(error)) from error
        # A second check covers changes made during staging/backup creation.
        if sha256(read_target(path)) != expected_sha256:
            raise WasdifyError("file_changed", path=str(path))
        os.replace(temporary, path)
        temporary = None
        return ApplyResult("applied", expected_sha256, result_hash, backup_path)
    except OSError as error:
        raise WasdifyError("write_failed", path=str(path), reason=str(error)) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Do not hide the original failure; the original EXE is untouched.

