"""Synthetic resource declarations, archives and injected transaction failures."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import os
import unittest
from unittest.mock import patch
import zipfile

from wasdify.models import AdditionalFile, WasdifyError
from wasdify.resources import read_resource
from wasdify.storage import sha256, write_set
from tests.support import temporary_directory
from tests.test_core import SOURCE, definition, game, service_for


PAYLOAD = b"synthetic additional file"


class AdditionalFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(temporary_directory()))
        self.path = self.directory / "renamed.exe"
        self.path.write_bytes(SOURCE)
        self.declaration = AdditionalFile("setup/ru.bin", None, "main", "SETUP.EXE", sha256(PAYLOAD))

    def service(self, declarations=None, second=False):
        patches = [replace(definition(), files=tuple(declarations or (self.declaration,)))]
        if second:
            patches.append(replace(definition(), id="second", files=(self.declaration,)))
        return service_for(game(*patches), probes={"test-game": lambda targets: True})

    def test_replace_backup_repeat_and_stale_destination(self):
        service = self.service()
        setup = self.directory / "SETUP.EXE"
        setup.write_bytes(b"old setup")
        with patch("wasdify.service.read_resource", return_value=PAYLOAD):
            plan = service.plan({"main": self.path}, ("first",))
            self.assertEqual(plan.files[0].action, "replace")
            setup.write_bytes(b"concurrent writer")
            with self.assertRaises(WasdifyError):
                service.apply(plan)
            self.assertEqual(self.path.read_bytes(), SOURCE)
            result = service.apply(service.plan({"main": self.path}, ("first",)))
            self.assertEqual(result.files[0].backup.read_bytes(), b"concurrent writer")
            self.assertEqual(setup.read_bytes(), PAYLOAD)
            self.assertEqual(service.apply(service.plan({"main": self.path}, ("first",))).status,
                             "no_changes")

    def test_missing_language_and_invalid_pak_block_without_writes(self):
        service = self.service((replace(self.declaration, language="ru"),))
        plan = service.plan({"main": self.path}, ("first",))
        self.assertIn("resource_language_required", [i.code for i in plan.issues])
        with patch("wasdify.service.read_resource", side_effect=WasdifyError("invalid_pak")):
            plan = self.service().plan({"main": self.path}, ("first",))
            self.assertFalse(plan.ready)
        self.assertEqual(self.path.read_bytes(), SOURCE)
        self.assertFalse((self.directory / "SETUP.EXE").exists())

    def test_path_conflicts_and_equal_files_merge(self):
        with patch("wasdify.service.read_resource", return_value=PAYLOAD):
            same = self.service((self.declaration, self.declaration))
            self.assertEqual(len(same.plan({"main": self.path}, ("first",)).files), 1)
            for dest in ("renamed.exe", "../outside", "/absolute", "C:/absolute", "a:stream"):
                with self.subTest(dest=dest):
                    service = self.service((replace(self.declaration, destination=dest),))
                    self.assertFalse(service.plan({"main": self.path}, ("first",)).ready)
            service = self.service((self.declaration,
                                    replace(self.declaration, destination="setup.exe", sha256="a"*64)))
            self.assertFalse(service.plan({"main": self.path}, ("first",)).ready)

    def test_no_resource_access_without_declared_files(self):
        with patch("wasdify.service.read_resource", side_effect=AssertionError("unexpected resource")):
            self.assertTrue(service_for().plan({"main": self.path}, ("first",)).ready)

    def test_resource_change_after_plan_blocks_before_any_write(self):
        service = self.service()
        with patch("wasdify.service.read_resource", return_value=PAYLOAD):
            plan = service.plan({"main": self.path}, ("first",))
        with patch("wasdify.service.read_resource", side_effect=WasdifyError("invalid_pak")):
            with self.assertRaises(WasdifyError):
                service.apply(plan)
        self.assertEqual(self.path.read_bytes(), SOURCE)
        self.assertFalse((self.directory/"SETUP.EXE").exists())

    def test_archive_hash_corruption_duplicate_and_frozen_location(self):
        from wasdify import resources
        archive_path = self.directory / "WASDify.pak"
        executable = self.directory / "WASDify.exe"
        for kind in ("valid", "duplicate", "unsafe", "corrupt", "missing"):
            with self.subTest(kind=kind):
                data = BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    archive.writestr("setup/ru.bin", PAYLOAD)
                    if kind == "duplicate":
                        archive.writestr("SETUP/RU.BIN", PAYLOAD)
                    if kind == "unsafe":
                        archive.writestr("../outside", b"bad")
                archive_path.write_bytes(b"broken" if kind == "corrupt" else data.getvalue())
                if kind == "missing":
                    archive_path.unlink()
                with patch.object(resources.sys, "frozen", True, create=True), \
                        patch.object(resources.sys, "executable", str(executable)):
                    if kind == "valid":
                        self.assertEqual(read_resource(self.declaration.source, sha256(PAYLOAD)), PAYLOAD)
                        with self.assertRaises(WasdifyError):
                            read_resource(self.declaration.source, "0"*64)
                        with self.assertRaises(WasdifyError):
                            read_resource("missing.bin", sha256(PAYLOAD))
                    else:
                        with self.assertRaises(WasdifyError):
                            read_resource(self.declaration.source, sha256(PAYLOAD))

    def test_symlink_and_junction_are_rejected(self):
        from wasdify.resources import destination
        # Windows reparse-point detection also works on Python 3.11.
        real_lstat = Path.lstat
        def reparse(path):
            result = real_lstat(path)
            if path == self.directory:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result
        with patch.object(Path, "lstat", reparse), self.assertRaises(WasdifyError):
            destination(self.path, "SETUP.EXE")


class TransactionTests(unittest.TestCase):
    def test_each_replace_failure_rolls_back_new_and_existing_files(self):
        real_replace = os.replace
        for backup in (False, True):
            for existing in (False, True):
                for fail_at in (1, 2, 3):
                    with self.subTest(backup=backup, existing=existing, fail_at=fail_at), temporary_directory() as directory:
                        root = Path(directory)
                        paths = [root / name for name in ("game.exe", "setup.exe", "nested/info.bin")]
                        paths[0].write_bytes(b"original")
                        if existing:
                            paths[1].write_bytes(b"old setup")
                        entries = tuple((p, sha256(p.read_bytes()) if p.exists() else None,
                                         b"new"+bytes((i,))) for i, p in enumerate(paths))
                        count = 0
                        def fail(source, destination):
                            nonlocal count
                            count += 1
                            if count == fail_at:
                                raise OSError("injected replacement failure")
                            return real_replace(source, destination)
                        with patch("wasdify.storage.os.replace", side_effect=fail), self.assertRaises(WasdifyError):
                            write_set(entries, backup=backup)
                        self.assertEqual(paths[0].read_bytes(), b"original")
                        if existing:
                            self.assertEqual(paths[1].read_bytes(), b"old setup")
                        else:
                            self.assertFalse(paths[1].exists())
                        self.assertFalse(paths[2].exists())
                        self.assertFalse(list(root.rglob("*.tmp")))
                        if not backup:
                            self.assertFalse(list(root.rglob("*.bak")))

    def test_stage_backup_and_revalidation_fail_before_replacement(self):
        for failure in ("stage", "backup", "changed"):
            with self.subTest(failure=failure), temporary_directory() as directory:
                path = Path(directory)/"game.exe"
                path.write_bytes(b"original")
                entries = ((path, sha256(b"original"), b"new"),)
                if failure == "stage":
                    context = patch("wasdify.storage.os.fsync", side_effect=OSError("stage"))
                elif failure == "backup":
                    original_open = Path.open
                    def fail_backup(p, *args, **kwargs):
                        if p.suffix == ".bak":
                            raise OSError("backup")
                        return original_open(p, *args, **kwargs)
                    context = patch.object(Path, "open", fail_backup)
                else:
                    entries = ((path, "0"*64, b"new"),)
                    from contextlib import nullcontext
                    context = nullcontext()
                with context, self.assertRaises(WasdifyError):
                    write_set(entries)
                self.assertEqual(path.read_bytes(), b"original")
                self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_change_during_staging_is_revalidated(self):
        import wasdify.storage as storage
        with temporary_directory() as directory:
            path = Path(directory)/"game.exe"
            path.write_bytes(b"original")
            original_fsync = os.fsync
            def change(descriptor):
                original_fsync(descriptor)
                path.write_bytes(b"concurrent writer")
            with patch.object(storage.os, "fsync", side_effect=change), self.assertRaises(WasdifyError):
                write_set(((path, sha256(b"original"), b"new"),), backup=False)
            self.assertEqual(path.read_bytes(), b"concurrent writer")
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_rollback_failure_reports_preserved_recovery(self):
        real_replace = os.replace
        with temporary_directory() as directory:
            root = Path(directory); first = root/"game.exe"; second = root/"setup.exe"
            first.write_bytes(b"original")
            count = 0
            def fail(source, destination):
                nonlocal count
                count += 1
                if count >= 2:
                    raise OSError("failure including rollback")
                return real_replace(source, destination)
            with patch("wasdify.storage.os.replace", side_effect=fail), self.assertRaises(WasdifyError) as caught:
                write_set(((first, sha256(b"original"), b"new"), (second, None, b"setup")), backup=False)
            self.assertEqual(caught.exception.issue.code, "rollback_failed")
            recovery = caught.exception.issue.params["recovery"]
            self.assertEqual(Path(recovery[str(first)]).read_bytes(), b"original")
            self.assertEqual(first.read_bytes(), b"new")
