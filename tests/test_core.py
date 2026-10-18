"""Only synthetic bytes; no copyrighted game files are test fixtures."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wasdify.catalog import Catalog, load_catalog
from wasdify.engine import build_result, order_patches
from wasdify.i18n import Translator, messages
from wasdify.models import (Change, Game, Issue, KnownVersion, PatchAnalysis,
                            PatchDefinition, WasdifyError)
from wasdify.service import Service
from wasdify.signatures import compile_pattern, find_matches, require_matches
from wasdify.storage import sha256, write_result


SOURCE = b"TEST-GAME:context:ABC:end"


def definition(name="first", **kwargs):
    return PatchDefinition(name, {"en": name, "ru": name}, {"en": name, "ru": name},
                           implemented=True, **kwargs)


def game(*patches, known=True):
    versions = (KnownVersion("test-v1", sha256(SOURCE), len(SOURCE), "en", "TEST.EXE"),) if known else ()
    return Game("test-game", {"en": "Test game", "ru": "Тестовая игра"}, ("main",),
                versions, tuple(patches or [definition()]))


class SyntheticPatch:
    def analyze(self, targets):
        data = targets["main"]
        before = find_matches(data, "63 6F 6E 74 65 78 74 3A 41 42 43 3A")
        after = find_matches(data, "63 6F 6E 74 65 78 74 3A 58 59 5A 3A")
        if len(before) + len(after) != 1:
            return PatchAnalysis(issues=(Issue("signature_count", {"expected": 1, "actual": len(before) + len(after)}),))
        if after:
            return PatchAnalysis(already_applied=True)
        return PatchAnalysis(changes=(Change("main", before[0] + 8, b"ABC", b"XYZ"),))


def service_for(test_game=None, implementations=None, probes=None):
    return Service(Catalog((test_game or game(),)),
                   implementations if implementations is not None else {("test-game", "first"): SyntheticPatch()},
                   probes or {})


class SignatureTests(unittest.TestCase):
    def test_mask_and_overlapping_matches(self):
        self.assertEqual(find_matches(b"\xaa\xaa\xaa", "AA ??"), (0, 1))

    def test_region_returns_absolute_offsets(self):
        self.assertEqual(find_matches(b"ABCABC", "41 ?? 43", start=1, end=6), (3,))
        self.assertEqual(find_matches(b"ABC", "41 42 43", end=2), ())

    def test_anchor_after_wildcard(self):
        self.assertEqual(find_matches(b"xxxABy", "?? 41 42"), (2,))

    def test_empty_or_short_input(self):
        self.assertEqual(find_matches(b"", "41"), ())
        self.assertEqual(find_matches(b"A", "41 42"), ())

    def test_bad_patterns(self):
        for value in ("", "??", "?? ??", "A", "GG", "0x41", "4?"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compile_pattern(value)

    def test_bad_regions(self):
        for start, end in ((-1, 2), (2, 1), (0, 4)):
            with self.assertRaises(ValueError):
                find_matches(b"ABC", "41", start=start, end=end)

    def test_missing_and_ambiguous(self):
        for data in (b"B", b"AA"):
            with self.assertRaises(WasdifyError) as caught:
                require_matches(data, "41")
            self.assertEqual(caught.exception.issue.code, "signature_count")
        self.assertEqual(require_matches(b"AA", "41", count=2), (0, 1))


class DependencyTests(unittest.TestCase):
    def test_dependency_order_and_deduplication(self):
        value = game(definition("base"), definition("child", dependencies=("base",)))
        self.assertEqual(order_patches(value, ("child", "base", "child")), ("base", "child"))

    def test_missing_dependency_is_not_added(self):
        value = game(definition("base"), definition("child", dependencies=("base",)))
        with self.assertRaises(WasdifyError) as caught:
            order_patches(value, ("child",))
        self.assertEqual(caught.exception.issue.code, "missing_dependency")

    def test_cycle_conflict_unknown_and_empty(self):
        cases = [
            (game(definition("a", dependencies=("b",)), definition("b", dependencies=("a",))), ("a", "b"), "dependency_cycle"),
            (game(definition("a", conflicts=("b",)), definition("b")), ("b", "a"), "patch_conflict"),
            (game(), ("missing",), "unknown_patch"),
            (game(), (), "no_patches"),
        ]
        for value, selected, code in cases:
            with self.subTest(code=code), self.assertRaises(WasdifyError) as caught:
                order_patches(value, selected)
            self.assertEqual(caught.exception.issue.code, code)


class EditTests(unittest.TestCase):
    def test_snapshot_relative_length_changing_edits(self):
        edits = (Change("main", 1, b"B", b"long"), Change("main", 4, b"E", b"!"))
        self.assertEqual(build_result({"main": b"ABCDE"}, edits)["main"], b"AlongCD!")

    def test_append(self):
        self.assertEqual(build_result({"main": b"A"}, (Change("main", 1, b"", b"B"),))["main"], b"AB")

    def test_invalid_preimage_ranges_and_overlaps(self):
        cases = [
            ((Change("main", 0, b"Z", b"X"),), "unexpected_bytes"),
            ((Change("main", -1, b"A", b"X"),), "invalid_change"),
            ((Change("main", 0, b"", b"X"),), "invalid_change"),
            ((Change("main", 2, b"CD", b"X"),), "invalid_change"),
            ((Change("main", 0, b"A", b"A"),), "invalid_change"),
            ((Change("other", 0, b"A", b"X"),), "missing_role"),
            ((Change("main", 0, b"AB", b"XX"), Change("main", 1, b"B", b"Y")), "overlapping_changes"),
        ]
        for edits, code in cases:
            with self.subTest(code=code), self.assertRaises(WasdifyError) as caught:
                build_result({"main": b"ABC"}, edits)
            self.assertEqual(caught.exception.issue.code, code)


class CatalogTests(unittest.TestCase):
    def test_packaged_catalog_has_one_real_patch_and_two_placeholders(self):
        value = load_catalog().game("dungeons-of-kremlin")
        self.assertEqual(len(value.versions), 2)
        self.assertEqual({p.id for p in value.patches}, {"wasd", "resolution-640x400", "render-optimization"})
        self.assertEqual({p.id for p in value.patches if p.implemented}, {"resolution-640x400"})
        self.assertTrue(all(not p.dependencies and not p.conflicts for p in value.patches))

    def test_invalid_metadata(self):
        value = game()
        for games in ((value, value), (replace(value, title={"en": "Only English"}),),
                      (replace(value, patches=(definition(), definition())),)):
            with self.assertRaises(ValueError):
                Catalog(games)

    def test_locale_keys_and_parameters_match(self):
        import string
        self.assertEqual(messages("en").keys(), messages("ru").keys())
        formatter = string.Formatter()
        for key in messages("en"):
            fields = lambda lang: {name for _, name, _, _ in formatter.parse(messages(lang)[key]) if name}
            self.assertEqual(fields("en"), fields("ru"), key)
        self.assertIn("заглушка", Translator("ru").issue(Issue("not_implemented", {"patch": "wasd"})))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "renamed.exe"
        self.path.write_bytes(SOURCE)
        self.service = service_for()

    def test_hash_detection_does_not_depend_on_name(self):
        detection = self.service.detect(self.path)
        self.assertEqual((detection.game_id, detection.version_id, detection.method), ("test-game", "test-v1", "hash"))

    def test_filename_is_only_hint(self):
        self.path = self.path.with_name("TEST.EXE")
        self.path.write_bytes(b"MZ-not-a-game")
        detection = self.service.detect(self.path)
        self.assertIsNone(detection.game_id)
        self.assertEqual(detection.hints, ("test-game",))
        self.assertEqual(self.service.plan({"main": self.path}, ("first",)).issues[0].code, "unknown_file")

    def test_unknown_hash_manual_signature_validation(self):
        self.path.write_bytes(b"extra" + SOURCE)
        plan = self.service.plan({"main": self.path}, ("first",), "test-game")
        self.assertTrue(plan.ready)
        self.assertEqual(plan.detection.method, "manual")
        self.assertEqual(plan.changes[0].offset, SOURCE.index(b"ABC") + 5)

    def test_unknown_invalid_file_is_blocked_even_with_manual_game(self):
        self.path.write_bytes(b"not-compatible")
        plan = self.service.plan({"main": self.path}, ("first",), "test-game")
        self.assertFalse(plan.ready)
        self.assertEqual(plan.issues[0].code, "signature_count")

    def test_probe_detection(self):
        service = service_for(game(known=False), probes={"test-game": lambda targets: targets["main"].startswith(b"TEST-GAME:")})
        self.assertEqual(service.detect(self.path).method, "signature")

    def test_ambiguous_probe_detection(self):
        service = Service(Catalog((game(known=False), replace(game(known=False), id="second"))), {},
                          {"test-game": lambda _: True, "second": lambda _: True})
        with self.assertRaises(WasdifyError) as caught:
            service.detect(self.path)
        self.assertEqual(caught.exception.issue.code, "ambiguous_game")

    def test_wrong_manual_game_does_not_override_hash(self):
        service = Service(Catalog((game(), replace(game(known=False), id="other"))), {})
        with self.assertRaises(WasdifyError) as caught:
            service.detect(self.path, "other")
        self.assertEqual(caught.exception.issue.code, "game_mismatch")

    def test_plan_does_not_write_and_apply_creates_backup(self):
        plan = self.service.plan({"main": self.path}, ("first",))
        self.assertTrue(plan.ready)
        self.assertEqual(self.path.read_bytes(), SOURCE)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)
        result = self.service.apply(plan)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.backup.read_bytes(), SOURCE)
        self.assertEqual(self.path.read_bytes(), SOURCE.replace(b"ABC", b"XYZ"))
        self.assertEqual(result.sha256_after, sha256(self.path.read_bytes()))

    def test_repeated_apply_is_noop_and_no_new_backup(self):
        self.service.apply(self.service.plan({"main": self.path}, ("first",)))
        plan = self.service.plan({"main": self.path}, ("first",), "test-game")
        self.assertEqual(plan.already_applied, ("first",))
        self.assertEqual(self.service.apply(plan).status, "no_changes")
        self.assertEqual(len(list(self.path.parent.glob("*.bak"))), 1)

    def test_backup_can_be_disabled(self):
        result = self.service.apply(self.service.plan({"main": self.path}, ("first",)), backup=False)
        self.assertIsNone(result.backup)
        self.assertFalse(list(self.path.parent.glob("*.bak")))

    def test_backup_is_unique(self):
        first = self.service.apply(self.service.plan({"main": self.path}, ("first",))).backup
        self.path.write_bytes(SOURCE)
        second = self.service.apply(self.service.plan({"main": self.path}, ("first",))).backup
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_file_changed_after_analysis(self):
        plan = self.service.plan({"main": self.path}, ("first",))
        self.path.write_bytes(b"changed")
        with self.assertRaises(WasdifyError) as caught:
            self.service.apply(plan)
        self.assertEqual(caught.exception.issue.code, "file_changed")
        self.assertEqual(list(self.path.parent.glob("*.bak")), [])

    def test_forged_or_changed_plan_rejected(self):
        plan = self.service.plan({"main": self.path}, ("first",))
        with self.assertRaises(WasdifyError) as caught:
            self.service.apply(replace(plan, changes=(Change("main", 0, b"TEST", b"FAKE"),)))
        self.assertEqual(caught.exception.issue.code, "plan_changed")
        self.assertEqual(self.path.read_bytes(), SOURCE)

    def test_stub_blocks_all_writes(self):
        value = game(replace(definition(), implemented=False))
        service = service_for(value)
        plan = service.plan({"main": self.path}, ("first",))
        self.assertEqual(plan.issues[0].code, "not_implemented")
        with self.assertRaises(WasdifyError):
            service.apply(plan)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)
        self.assertEqual(self.path.read_bytes(), SOURCE)

    def test_missing_implementation_and_invalid_analysis(self):
        service = service_for(implementations={})
        self.assertEqual(service.plan({"main": self.path}, ("first",)).issues[0].code, "implementation_missing")
        implementation = unittest.mock.Mock()
        implementation.analyze.return_value = PatchAnalysis()
        service = service_for(implementations={("test-game", "first"): implementation})
        self.assertEqual(service.plan({"main": self.path}, ("first",)).issues[0].code, "invalid_analysis")

    def test_multiple_files_rejected_before_reading(self):
        with self.assertRaises(WasdifyError) as caught:
            self.service.plan({"main": "missing", "other": "also-missing"}, ("first",))
        self.assertEqual(caught.exception.issue.code, "single_file_only")

    def test_io_error(self):
        with self.assertRaises(WasdifyError) as caught:
            self.service.detect(self.path.parent)
        self.assertEqual(caught.exception.issue.code, "io_error")

    def test_backup_failure_preserves_original(self):
        real_open = Path.open

        def failing_open(path, mode="r", *args, **kwargs):
            if mode == "xb":
                raise PermissionError("simulated")
            return real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", failing_open), self.assertRaises(WasdifyError) as caught:
            write_result(self.path, sha256(SOURCE), b"patched")
        self.assertEqual(caught.exception.issue.code, "backup_failed")
        self.assertEqual(self.path.read_bytes(), SOURCE)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_replace_failure_preserves_original_and_verified_backup(self):
        with patch("wasdify.storage.os.replace", side_effect=PermissionError("simulated")), self.assertRaises(WasdifyError) as caught:
            write_result(self.path, sha256(SOURCE), b"patched")
        self.assertEqual(caught.exception.issue.code, "write_failed")
        self.assertEqual(self.path.read_bytes(), SOURCE)
        self.assertEqual(next(self.path.parent.glob("*.bak")).read_bytes(), SOURCE)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_change_during_staging_is_detected(self):
        from wasdify import storage
        original_read = storage.read_target
        reads = 0

        def racing_read(path):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.path.write_bytes(b"external-change")
            return original_read(path)

        with patch("wasdify.storage.read_target", side_effect=racing_read), self.assertRaises(WasdifyError) as caught:
            write_result(self.path, sha256(SOURCE), b"patched", backup=False)
        self.assertEqual(caught.exception.issue.code, "file_changed")
        self.assertEqual(self.path.read_bytes(), b"external-change")
        self.assertFalse(list(self.path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()

