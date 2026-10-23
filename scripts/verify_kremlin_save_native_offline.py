"""Prove native disk save/load with no webserver and no host memory API.

The test-only guest driver supplies input and a controlled health change. It
contains no serialization or restoration and is excluded from playable builds.
"""
from datetime import datetime
import argparse
import hashlib
import json
import os
import struct
import subprocess
import time
from zlib import crc32

from prepare_kremlin_save_native import prepare, ROOT
from verify_kremlin_save_io import inject
from wasdify.engine import build_result
from wasdify.patches.kremlin_weapons import WeaponSelectionFix
from wasdify.patches.kremlin_wasd import segments


def run(*, visible=False):
    work = ROOT / 'mqa' / ('save-native-offline-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    nasm = ROOT / 'build/tools/nasm-2.16.03/nasm.exe'
    prepare(work, nasm)
    wrapper = work / 'offline-test.asm'
    wrapper.write_text('%define KRS_NATIVE_OFFLINE_TEST 1\n' +
        ('%define KRS_NATIVE_OFFLINE_KEEP_OPEN 1\n' if visible else '') + '%include "kremlin_save_native.asm"\n'
        '%include "kremlin_save_native_offline.inc"\n', encoding='ascii')
    payload = work / 'offline-test.bin'
    subprocess.run([str(nasm), '-f', 'bin', '-I', str(ROOT / 'src/wasdify/patches') + '/',
        '-I', str(ROOT / 'scripts') + '/', str(wrapper), '-o', str(payload)], check=True)
    source = (ROOT / 'originals/games/pkr/RUS/PKR.EXE').read_bytes()
    patched = inject(source, payload.read_bytes(), 'ru', data_size=65536, pause_on_start=True)
    fix = WeaponSelectionFix().analyze({'main': patched})
    assert not fix.issues
    instrumented = bytearray(build_result({'main': patched}, fix.changes)['main'])
    main = next(s for s in segments(instrumented) if s.index == 2)
    # The native attempt announcement waits in a keyboard-only loop, with no
    # clock-start callbacks for this test driver. In THIS TEST COPY only, pass
    # the native dialog's nonblocking argument, retaining drawing and attempt
    # increment. The playable payload and its preparation never do this.
    for offset in (0x0f4e, 0x0f5d, 0x0f6c):
        assert instrumented[main.start + offset:main.start + offset + 2] == b'\x6a\0'
        instrumented[main.start + offset + 1] = 1
    (work / 'RUS/PKR.EXE').write_bytes(instrumented)
    (work / 'launcher.json').write_text(json.dumps({'experimental': True,
        'test_only_guest_input': True, 'webserver': False,
        'test_payload_sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}, indent=2), encoding='ascii')
    env = dict(os.environ)
    if not visible:
        env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    report = {'status': 'running', 'webserver': False, 'host_memory_api': False,
              'guest_test_driver': 'input and controlled health change only'}
    try:
        with (work / 'stdout.log').open('wb') as out, (work / 'stderr.log').open('wb') as err:
            guest = subprocess.Popen([str(ROOT / 'mqa/dosbox/dosbox.exe'), '-conf', str(work / 'dosbox.conf'),
                '-noconsole'], cwd=ROOT / 'mqa/dosbox', env=env, stdout=out, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW)
            report['process_id'] = guest.pid
            try:
                if visible:
                    end = time.monotonic() + 60
                    while not (work / 'RUS/KRNTEST.OK').exists():
                        if guest.poll() is not None or time.monotonic() > end:
                            raise RuntimeError('offline_visible_driver_failed')
                        time.sleep(.1)
                else:
                    guest.wait(timeout=60)
            finally:
                if guest.poll() is None and not (visible and (work / 'RUS/KRNTEST.OK').exists()):
                    guest.terminate()
                    guest.wait(timeout=10)
        assert (work / 'RUS/KRNTEST.OK').read_bytes() == b'native F5/F9 and complete recapture passed\r\n'
        raw = (work / 'RUS/KRN1.SAV').read_bytes()
        assert struct.unpack_from('<H', raw, 28)[0] == len(raw)
        wire = bytearray(raw)
        wire[40:44] = bytes(4)
        assert crc32(wire) == struct.unpack_from('<I', raw, 40)[0]
        report.update(status='passed', save_size=len(raw))
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='ascii')
        print(work, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--visible', action='store_true', help='Leave the successful test copy open for visual QA')
    print(json.dumps(run(visible=parser.parse_args().visible), indent=2))
