"""Prepare an isolated playable autonomous RUS level-1 copy (development only).

The generated launcher runs DOSBox directly, with its webserver disabled.
Python and NASM are build tools only. Never replaces an existing play directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from build_kremlin_save_native import build, ROOT
from verify_kremlin_save_io import inject, PROFILES
from wasdify.engine import build_result
from wasdify.patches.kremlin_weapons import WeaponSelectionFix


def prepare(directory, nasm, *, test_gates=False):
    directory = directory.resolve()
    directory.relative_to((ROOT / 'mqa').resolve())
    if directory.exists():
        raise ValueError('native_play_directory_exists')
    reference = ROOT / 'originals/games/pkr/RUS'
    source = (reference / 'PKR.EXE').read_bytes()
    if hashlib.sha256(source).hexdigest() != PROFILES['ru'][4]:
        raise ValueError('native_reference_version')
    payload = ROOT / 'build/save-research/native-ru.bin'
    digest = build(nasm, payload, test_gates=test_gates)
    patched = inject(source, payload.read_bytes(), 'ru', data_size=65536, pause_on_start=True)
    fix = WeaponSelectionFix().analyze({'main': patched})
    if fix.issues:
        raise ValueError(fix.issues)
    patched = build_result({'main': patched}, fix.changes)['main']
    shutil.copytree(reference, directory / 'RUS')
    (directory / 'RUS/PKR.EXE').write_bytes(patched)
    (directory / 'native-ru.bin').write_bytes(payload.read_bytes())
    (directory / 'launcher.json').write_text(json.dumps({
        'experimental': True, 'native_serializer': True, 'language': 'ru', 'level': 1,
        'payload_sha256': digest, 'source_sha256': PROFILES['ru'][4],
        'exe_sha256': hashlib.sha256(patched).hexdigest(),
        'format': 'KRN1', 'webserver': False, 'research_gates': test_gates}, indent=2), encoding='ascii')
    config = directory / 'dosbox.conf'
    config.write_text(f'''[sdl]
fullscreen = off
[dosbox]
machine = svga_s3
memsize = 32
[cpu]
core = normal
cputype = pentium
cpu_cycles = 60000
cpu_cycles_protected = 60000
[webserver]
webserver_enabled = off
[autoexec]
mount -u c
mount c "{directory / 'RUS'}"
c:
PKR.EXE -r0
exit
''', encoding='ascii')
    (directory / 'start.cmd').write_text(
        '@echo off\n' + f'cd /d "{ROOT / "mqa/dosbox"}"\n' +
        f'start "" "{ROOT / "mqa/dosbox/dosbox.exe"}" -conf "{config}" -noconsole\n', encoding='ascii')
    return directory / 'start.cmd'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'mqa/save-native-ru')
    parser.add_argument('--nasm', type=Path, default=ROOT / 'build/tools/nasm-2.16.03/nasm.exe')
    args = parser.parse_args()
    print(prepare(args.directory, args.nasm))
