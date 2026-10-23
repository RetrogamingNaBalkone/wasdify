"""Conditional extensions, validated installed state and atomic SETUP updates."""
from dataclasses import replace
import json
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from tests.support import temporary_directory
from tests.test_core import definition, game
from tests.test_kremlin_save_patch import executable, SAVE, SETTINGS
from wasdify.catalog import Catalog
from wasdify.engine import build_result, order_patches
from wasdify.models import PatchExtension, WasdifyError
from wasdify.patches import kremlin_settings as settings, kremlin_save_native as saves
from wasdify.resources import read_resource
from wasdify.service import Service


class ExtensionMetadataTests(unittest.TestCase):
    def test_extension_references_and_translations(self):
        valid = PatchExtension('extra', 'base', {'ru': 'Эффект', 'en': 'Effect'})
        Catalog((game(definition('base'), definition('child', extensions=(valid,))),))
        for extension in (replace(valid, target='missing'), replace(valid, target='child'),
                          replace(valid, id=''), replace(valid, description={'en': 'Effect'})):
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                Catalog((game(definition('base'), definition('child', extensions=(extension,))),))
        with self.assertRaises(ValueError):
            Catalog((game(definition('base'), definition('child', extensions=(valid, valid))),))

    def test_installed_dependency_and_reverse_conflict(self):
        value = game(definition('base'), definition('child', dependencies=('base',)))
        self.assertEqual(order_patches(value, ('child',), ('base',)), ('child',))
        value = game(definition('base', conflicts=('child',)), definition('child'))
        with self.assertRaises(WasdifyError):
            order_patches(value, ('child',), ('base',))


class ExtensionInstallationTests(unittest.TestCase):
    def prepare(self, language):
        root = self.enterContext(temporary_directory())
        path = root / 'renamed.exe'
        path.write_bytes(executable(language))
        return Service(), path

    def install(self, service, path, selection):
        plan = service.plan({'main': path}, selection)
        self.assertTrue(plan.ready, plan.issues)
        service.apply(plan)
        return plan

    def test_both_languages_orders_installed_requirements_and_repair(self):
        for language in ('ru', 'en'):
            expected = None
            for stages in ((SAVE+SETTINGS,), (SETTINGS, SAVE), (SAVE, SETTINGS)):
                with self.subTest(language=language, stages=stages):
                    service, path = self.prepare(language)
                    self.assertEqual(service.inspect(path).installed, ())
                    for selection in stages:
                        plan = self.install(service, path, selection)
                        self.assertEqual(plan.selected, selection)
                    if expected is None:
                        expected = path.read_bytes()
                    self.assertEqual(expected, path.read_bytes())
                    self.assertEqual(plan.active_extensions, ('save-load-settings',))
                    inspection = service.inspect(path)
                    self.assertEqual(set(inspection.installed), set(SAVE+SETTINGS))
                    inner = saves.restore_base(path.read_bytes())
                    self.assertTrue(settings.save_enabled(inner))
                    setup = path.parent / 'SETUP.EXE'
                    declaration = next(f for p in service.list_patches('dungeons-of-kremlin')
                                       if p.id == 'game-settings' for f in p.files
                                       if f.language == language and f.variant == 'save-load')
                    self.assertEqual(setup.read_bytes(), read_resource(declaration.source, declaration.sha256))
                    # Previously installed prerequisites need not be selected again.
                    repeat = service.plan({'main': path}, ('save-load',))
                    self.assertTrue(repeat.ready, repeat.issues)
                    self.assertFalse(repeat.changes)
                    self.assertFalse(repeat.updated)
                    self.assertEqual(service.apply(repeat).status, 'no_changes')
                    setup.unlink()
                    repair = self.install(service, path, ('save-load',))
                    self.assertEqual(repair.updated, ('game-settings',))
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertEqual(setup.read_bytes(), read_resource(declaration.source, declaration.sha256))

    def test_automatic_update_backup_ini_and_rollback(self):
        for language in ('ru', 'en'):
            with self.subTest(language=language):
                service, path = self.prepare(language)
                self.install(service, path, SETTINGS+('weapon-selection-fix',))
                setup = path.parent / 'SETUP.EXE'
                ini = path.parent / 'KREMLIN.INI'
                ini.write_bytes(b'[wasdify]\nversion=1\n[keys]\nfire=63,0\n')
                before = path.read_bytes(), setup.read_bytes(), ini.read_bytes()
                self.assertFalse(settings.save_enabled(before[0]))
                plan = service.plan({'main': path}, ('save-load',))
                self.assertTrue(plan.ready, plan.issues)
                self.assertEqual(plan.updated, ('game-settings',))
                self.assertEqual(plan.files[0].action, 'replace')
                real_replace = os.replace
                failed = False
                def fail_setup(source, destination):
                    nonlocal failed
                    if destination == setup and not failed:
                        failed = True
                        raise OSError('injected SETUP failure')
                    return real_replace(source, destination)
                with patch('wasdify.storage.os.replace', side_effect=fail_setup), self.assertRaises(WasdifyError):
                    service.apply(plan)
                self.assertEqual(before, (path.read_bytes(), setup.read_bytes(), ini.read_bytes()))
                result = service.apply(plan)
                self.assertEqual(result.backup.read_bytes(), before[0])
                self.assertEqual(result.files[0].backup.read_bytes(), before[1])
                self.assertEqual(ini.read_bytes(), before[2])
                for field in ('active_extensions', 'installed', 'updated'):
                    fresh = service.plan({'main': path}, ('save-load',))
                    with self.assertRaises(WasdifyError):
                        service.apply(replace(fresh, **{field: ('forged',)}))

    def test_standalone_saves_do_not_install_settings(self):
        service, path = self.prepare('ru')
        plan = self.install(service, path, SAVE)
        self.assertFalse(plan.active_extensions)
        self.assertFalse(plan.files)
        self.assertFalse((path.parent / 'SETUP.EXE').exists())
        self.assertNotIn('game-settings', service.inspect(path).installed)

    def test_unrelated_fix_does_not_report_unchanged_settings_as_updated(self):
        service, path = self.prepare('ru')
        self.install(service, path, SETTINGS)
        plan = self.install(service, path, ('weapon-selection-fix',))
        self.assertTrue(plan.changes)
        self.assertFalse(plan.updated)
        self.assertEqual(plan.files[0].action, 'no_changes')

    def test_cli_reports_installed_and_active_extension(self):
        from wasdify.cli import main
        service, path = self.prepare('en')
        self.install(service, path, SETTINGS+('weapon-selection-fix',))
        for json_output in (False, True):
            with redirect_stdout(StringIO()) as output:
                code = main(['plan', str(path), '--patch', 'save-load', '--lang', 'en']
                            + (['--json'] if json_output else []))
            self.assertEqual(code, 0, output.getvalue())
            if json_output:
                plan = json.loads(output.getvalue())['plan']
                self.assertEqual(plan['selected'], ['save-load'])
                self.assertEqual(plan['active_extensions'], ['save-load-settings'])
                self.assertEqual(plan['updated'], ['game-settings'])
            else:
                self.assertIn('SETUP.EXE will be updated', output.getvalue())
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(['detect', str(path), '--json']), 0)
        self.assertIn('game-settings', json.loads(output.getvalue())['installed'])

    def test_legacy19_legacy21_and_legacy_saved_installations(self):
        for language in ('ru', 'en'):
            for legacy in ('legacy', 'legacy21'):
                for with_saves in (False, True) if legacy == 'legacy21' else (False,):
                    with self.subTest(language=language, legacy=legacy, saves=with_saves):
                        service, path = self.prepare(language)
                        raw = path.read_bytes()
                        edits = tuple(c for p in SETTINGS[:2]+('weapon-selection-fix',)
                                      for c in service.implementations[('dungeons-of-kremlin', p)].analyze({'main': raw}).changes)
                        base = build_result({'main': raw}, edits)['main']
                        payload = json.loads(settings.files('wasdify').joinpath(
                            f'data/kremlin_settings_{legacy}.json').read_text('ascii'))
                        old = settings._install(base, payload)
                        if with_saves:
                            old = saves._install(old)
                        path.write_bytes(old)
                        self.install(service, path, ('save-load',))
                        inner = saves.restore_base(path.read_bytes())
                        self.assertTrue(settings.save_enabled(inner))
                        self.assertEqual(service.apply(service.plan({'main': path}, ('save-load',))).status, 'no_changes')

    def test_damaged_settings_block_inspection_and_unrelated_patch(self):
        service, path = self.prepare('ru')
        self.install(service, path, SETTINGS)
        bad = bytearray(path.read_bytes())
        bad[-1] ^= 1
        path.write_bytes(bad)
        with self.assertRaises(WasdifyError):
            service.inspect(path)
        plan = service.plan({'main': path}, ('weapon-selection-fix',))
        self.assertFalse(plan.ready)
        self.assertEqual(path.read_bytes(), bad)


if __name__ == '__main__':
    unittest.main()
