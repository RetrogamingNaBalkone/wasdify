"""Audit step 6 deliverables and smoke-test wheel resources outside the repo.

Run with the EXE build environment (pip and PyInstaller are development tools).
The optional second Python verifies the supported minimum, Python 3.11.
"""
import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def audit_entries(entries, original_hashes):
    count = 0
    for name, raw in entries:
        parts = name.replace('\\', '/').lower().split('/')
        assert not {'originals', 'mqa', '__pycache__'}.intersection(parts), name
        assert not any(word in name.lower() for word in
                       ('play_kremlin_save', 'profile_kremlin', 'failure-memory', '.dmp')), name
        assert sha256(raw).hexdigest() not in original_hashes, name
        if name.endswith('.pak'):
            with zipfile.ZipFile(io.BytesIO(raw)) as nested:
                count += audit_entries(((n, nested.read(n)) for n in nested.namelist()), original_hashes)
        count += 1
    return count


SMOKE = r'''
import json, sys
from pathlib import Path
target, repo = map(Path, sys.argv[1:3])
sys.path = [str(target)] + [p for p in sys.path if Path(p).resolve() not in (repo, repo/'src', repo/'scripts')]
import wasdify
from wasdify.service import Service
from wasdify.i18n import messages
from wasdify.patches.kremlin_save_native import resources, restore_base
from wasdify.patches.kremlin_settings import restore_base as restore_settings
from wasdify.resources import read_resource
assert Path(wasdify.__file__).is_relative_to(target)
assert Path.cwd() == target
assert set(messages('ru')) == set(messages('en'))
service = Service()
chosen = ('weapon-selection-fix','save-load','wasd','resolution-640x400','game-settings')
for lang in ('ru', 'en'):
    path = target/lang/'synthetic.exe'
    plan = service.plan({'main':path}, chosen)
    assert plan.ready, plan.issues
    assert service.apply(plan).status == 'applied'
    assert service.apply(service.plan({'main':path}, chosen)).status == 'no_changes'
    # Layer removal retains the selected simple patches in the base image.
    baseline = restore_settings(restore_base(path.read_bytes()))
    for patch in ('weapon-selection-fix', 'wasd', 'resolution-640x400'):
        assert service.implementations[('dungeons-of-kremlin', patch)].analyze({'main':baseline}).already_applied
    declaration = next(f for p in service.list_patches('dungeons-of-kremlin')
                       if p.id == 'game-settings' for f in p.files if f.language == lang)
    assert (path.parent/'SETUP.EXE').read_bytes() == read_resource(declaration.source, declaration.sha256)
for raw in resources()['runtime'].values():
    payload = bytes.fromhex(raw)
    assert b'KRSFAIL1' not in payload and b'KRSCFG01' in payload
assert 'tkinter' not in sys.modules
print(json.dumps(dict(status='passed', python=sys.version.split()[0], module=wasdify.__file__,
                      installed_resources_and_synthetic_apply=['ru','en'])))
'''


def run(wheel, sdist, installer, python311):
    from PyInstaller.archive.readers import CArchiveReader
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
    from tests.test_kremlin_save_patch import executable
    from build_kremlin_save_native import build
    from wasdify.patches.kremlin_save_native import resources

    originals = {sha256(p.read_bytes()).hexdigest() for p in
                 (ROOT / 'originals/games/pkr').rglob('*') if p.is_file()}
    assert originals
    report = {'status': 'running', 'artifacts': {}}
    for artifact in (wheel, sdist, installer, installer.with_suffix('.pak')):
        report['artifacts'][artifact.name] = {'sha256': sha256(artifact.read_bytes()).hexdigest(),
                                             'bytes': artifact.stat().st_size}
    with zipfile.ZipFile(wheel) as archive:
        report['wheel_entries'] = audit_entries(((n, archive.read(n)) for n in archive.namelist()), originals)
        assert not any(n.startswith(('tests/', 'scripts/')) for n in archive.namelist())
        assert 'wasdify/data/kremlin_save_native.json' in archive.namelist()
    with tarfile.open(sdist) as archive:
        report['sdist_entries'] = audit_entries(
            ((m.name, archive.extractfile(m).read()) for m in archive.getmembers() if m.isfile()), originals)
    archive = CArchiveReader(str(installer))
    report['exe_entries'] = audit_entries(((n, archive.extract(n)) for n in archive.toc
                                         if archive.toc[n][-1] not in ('o',)), originals)
    modules = archive.open_embedded_archive('PYZ.pyz').toc
    assert not any(n.startswith(('scripts.', 'tests.', 'play_kremlin_save', 'profile_kremlin')) for n in modules)
    report['exe_python_modules'] = len(modules)
    with zipfile.ZipFile(installer.with_suffix('.pak')) as archive:
        report['pak_entries'] = audit_entries(((n, archive.read(n)) for n in archive.namelist()), originals)
    for language, raw in resources()['runtime'].items():
        output = ROOT / 'build/save-research' / ('release-reproduced-' + language + '.bin')
        build(ROOT / 'build/tools/nasm-2.16.03/nasm.exe', output, language=language)
        assert output.read_bytes() == bytes.fromhex(raw)
    report['reproducible_payloads'] = ['ru', 'en']
    target = Path(tempfile.mkdtemp(prefix='wasdify-step6-installed-'))
    assert not target.is_relative_to(ROOT)
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-compile',
                    '--target', str(target), str(wheel)], check=True, env=environment)
    report['installed_checks'] = []
    for python in (Path(sys.executable), python311):
        for language in ('ru', 'en'):
            folder = target / language
            folder.mkdir(exist_ok=True)
            raw = executable(language)
            (folder / 'synthetic.exe').write_bytes(raw)
            (folder / 'source.bin').write_bytes(raw)
        completed = subprocess.run([str(python), '-I', '-c', SMOKE, str(target), str(ROOT)],
                                   cwd=target, env=environment, capture_output=True,
                                   text=True, timeout=60)
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        report['installed_checks'].append(json.loads(completed.stdout))
    report['status'] = 'passed'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-dir', type=Path, default=ROOT / 'build/step6-package')
    parser.add_argument('--installer', type=Path, default=ROOT / 'dist/WASDify.exe')
    parser.add_argument('--python311', type=Path, default=ROOT / 'build/python311/python.exe')
    args = parser.parse_args()
    report = run(next(args.package_dir.glob('*.whl')), next(args.package_dir.glob('*.tar.gz')),
                 args.installer, args.python311)
    (args.package_dir / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
