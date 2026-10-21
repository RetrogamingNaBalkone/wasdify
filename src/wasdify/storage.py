"""Single-file writes: stage, verify, optional exclusive backup, replace."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from uuid import uuid4

from .models import ApplyResult, FileResult, WasdifyError


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


def write_set(entries: tuple[tuple[Path, str | None, bytes], ...],
              *, backup: bool = True) -> tuple[FileResult, ...]:
    """Stage a complete set; roll back ordinary failures without persistent backups."""
    from .resources import check_path
    staged = {}
    recovery = {}
    originals = {}
    backups = {}
    committed = []
    directories = []
    keep_recovery = False

    def current(path):
        check_path(path)
        return read_target(path) if path.exists() else None

    def verify(path, expected):
        data = current(path)
        if (sha256(data) if data is not None else None) != expected:
            raise WasdifyError("file_changed", path=str(path))
        return data

    def stage(path, payload, mode=None):
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name+".wasdify-",
                                         suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            except BaseException:
                output.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            if temporary.read_bytes() != payload:
                raise OSError("verification_failed")
            if mode is not None:
                os.chmod(temporary, mode)
            return temporary
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    try:
        # Validate the whole set before creating anything.
        for path, expected, payload in entries:
            originals[path] = verify(path, expected)
        for path, expected, payload in entries:
            original = originals[path]
            if original == payload:
                continue
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                check_path(directory)
                directory.mkdir()
                directories.append(directory)
            mode = stat.S_IMODE(path.stat().st_mode) if original is not None else None
            staged[path] = stage(path, payload, mode)
            if original is not None:
                recovery[path] = stage(path, original, mode)
                if backup:
                    saved = path.with_name(path.name+".wasdify-"+uuid4().hex+".bak")
                    created = False
                    try:
                        with saved.open("xb") as output:
                            created = True
                            output.write(original)
                            output.flush()
                            os.fsync(output.fileno())
                        if saved.read_bytes() != original:
                            raise OSError("verification_failed")
                    except OSError as error:
                        if created:
                            saved.unlink(missing_ok=True)
                        raise WasdifyError("backup_failed", path=str(saved)) from error
                    backups[path] = saved
        for path, expected, payload in entries:
            verify(path, expected)
        for path, expected, payload in entries:
            if path not in staged:
                continue
            verify(path, expected)
            os.replace(staged[path], path)
            committed.append(path)
            if read_target(path) != payload:
                raise OSError("verification_failed")
        return tuple(FileResult("no_changes" if originals[p] == data else "applied",
                                p, sha256(data), backups.get(p)) for p, _, data in entries)
    except (OSError, WasdifyError) as error:
        failed = []
        payloads = {p: data for p, _, data in entries}
        for path in reversed(committed):
            try:
                # Do not overwrite a concurrent writer during rollback.
                if current(path) != payloads[path]:
                    raise OSError("changed_during_rollback")
                if originals[path] is None:
                    path.unlink()
                else:
                    os.replace(recovery[path], path)
            except (OSError, WasdifyError):
                failed.append(str(path))
        if failed:
            keep_recovery = True
            raise WasdifyError("rollback_failed", paths=failed,
                               recovery={str(p): str(r) for p, r in recovery.items() if r.exists()},
                               backups={str(p): str(b) for p, b in backups.items()},
                               changed=[str(p) for p in committed if str(p) in failed]) from error
        if isinstance(error, WasdifyError):
            raise
        raise WasdifyError("write_failed", path=str(path)) from error
    finally:
        for temporary in (*staged.values(), *(() if keep_recovery else recovery.values())):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass

