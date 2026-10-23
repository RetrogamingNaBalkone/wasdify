"""Exercise autonomous failure/recovery paths in isolated real DOSBox copies.

Host actions supply input and arrange disk faults between guest operations.
Only the guest serializes and restores. Test-only fault 438 stops a restore
after native reconstruction; it is absent from ordinary playable payloads.
"""
import argparse
from datetime import datetime
import json
import struct
import time
from zlib import crc32

from prepare_kremlin_save_native import prepare, ROOT
from verify_kremlin_save_native import process, records
from verify_kremlin_settings_dosbox import wait_for
from build_kremlin_save_native import layout_fields


def invalid_health(raw):
    data = bytearray(raw)
    cursor = 64 + 16384 + struct.unpack_from('<H', data, 32)[0] * 6
    for _ in range(struct.unpack_from('<H', data, 30)[0]):
        size, kind, variant = struct.unpack_from('<HBB', data, cursor)
        if kind == 1:
            at = cursor + 16
            for name, (_, wire) in layout_fields(kind, variant).items():
                if name == 'health':
                    struct.pack_into('<h', data, at, 0)
                    data[40:44] = bytes(4)
                    struct.pack_into('<I', data, 40, crc32(data))
                    assert any(r['fields'].get('health') == 0 for r in records(data).values())
                    return bytes(data)
                at += struct.calcsize('<' + wire)
        cursor += size
    raise AssertionError('invented corruption did not find player')


def failed_request(d, scan):
    sequence = d.view().u16(d.transport + 400)
    d.key(scan)
    wait_for(lambda: d.view().u16(d.transport + 242) == 1)
    wait_for(lambda: d.view().u16(d.transport + 200) == d.view().u16(d.transport + 228) == 0)
    m = d.view()
    assert m.u16(d.transport + 400) == sequence
    assert m.u16(d.transport + 202) == 0
    message = m.read(d.transport + 1536, 256).split(b'\0')[0].decode('cp866')
    # Acknowledge a diagnostic, without changing native world/input/file data.
    d.api.put(str(d.transport + 242), bytes(2))
    return message


def run(language='ru', level=1):
    work = ROOT / 'mqa' / ('save-failures-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    prepare(work, ROOT / 'build/tools/nasm-2.16.03/nasm.exe', test_gates=True, level=level, language=language)
    report = {'language': language, 'level': level, 'status': 'running'}
    try:
        with process(work, 'failures', report, level, language) as d:
            d.enter()
            raw = d.save()
            health = d.check().u16(d.hero() + 0x5e)
            latest = d.save()
            assert d.save_path('PRV').read_bytes() == raw

            d.save_path('TMP').write_bytes(b'foreign temporary file')
            message = failed_request(d, 0x3f)
            assert '.TMP' in message
            assert d.save_path().read_bytes() == latest
            assert d.save_path('PRV').read_bytes() == raw
            assert d.save_path('TMP').read_bytes() == b'foreign temporary file'
            d.load(health)
            # Delete only the foreign marker created above in this unique copy.
            d.save_path('TMP').unlink()
            report['foreign_tmp_preserved_with_actionable_message'] = message

            # A real DOS create error (directory occupying the exclusive name).
            d.save_path('TMP').mkdir()
            failed_request(d, 0x3f)
            assert d.save_path().read_bytes() == latest
            assert d.save_path('TMP').is_dir()
            d.save_path('TMP').rmdir()
            d.load(health)
            report['dos_create_refusal_keeps_game_and_save'] = True

            d.save_path().write_bytes(invalid_health(latest))
            d.load(health)
            assert d.view().u16(d.transport + 434) == 1
            assert d.save_path('PRV').read_bytes() == raw
            report['crc_valid_invalid_state_falls_back_before_mutation'] = True
            d.save()
            assert d.save_path('PRV').read_bytes() == raw
            report['saving_after_corrupt_sav_preserves_prv'] = True

            # Both generations invalid: F9 must fail before touching the hero.
            d.save_path().write_bytes(invalid_health(latest))
            d.save_path('PRV').write_bytes(b'broken')
            hero = d.hero()
            d.api.put(str(hero + 0x5e), struct.pack('<H', max(1, health - 7)))
            failed_request(d, 0x43)
            assert d.hero() == hero
            assert d.check().u16(hero + 0x5e) == max(1, health - 7)
            assert d.check().u16(d.transport + 202) == 0
            report['all_invalid_no_world_mutation'] = True

            # Disk state after SAV->PRV, before TMP->SAV. PRV wins over TMP.
            d.save_path().unlink()
            d.save_path('PRV').write_bytes(raw)
            d.save_path('TMP').write_bytes(latest)
            d.load(health)
            assert d.view().u16(d.transport + 434) == 1
            report['interrupted_rotation_loads_previous'] = True
            # Interrupted first save: the verified TMP is the only candidate.
            d.save_path('PRV').unlink()
            d.load(health)
            assert d.view().u16(d.transport + 434) == 2
            assert d.save_path('TMP').read_bytes() == latest
            report['interrupted_first_save_loads_tmp'] = True

        # A new process must offer and restore the orphan TMP before the intro.
        with process(work, 'restart', report, level, language) as d:
            d.enter(choice='load')
            assert d.view().u16(d.transport + 434) == 2
            assert d.check().u16(d.hero() + 0x5e) == health
            report['orphan_tmp_loads_in_new_process'] = True
            # Controlled failure after native reconstruction must never resume.
            sequence = d.check().u16(d.transport + 400)
            d.api.put(str(d.transport + 438), b'\x01\0')
            d.key(0x43)
            wait_for(lambda: d.view().u16(d.transport + 254) == 2)
            assert d.view().u16(d.transport + 202) == 1
            assert d.view().u16(d.transport + 400) == sequence
            time.sleep(.3)
            assert d.view().u16(d.transport + 254) == 2
            assert d.view().u16(d.transport + 400) == sequence
            assert d.save_path('TMP').read_bytes() == latest
            report['post_mutation_failure_waits_without_resuming'] = True
            # Releasing without pressing must leave the fatal screen visible.
            d.api.put(str(d.ds + d.shift + 0x2171), b'\x02')
            time.sleep(.2)
            assert d.view().u16(d.transport + 254) == 2
            d.key(0x1c)
            # DOSBox exits after the guest's INT 21h exit and autoexec 'exit'.
            def exited():
                try:
                    d.api.get('dosbox/info')
                except OSError:
                    return True
                return False
            wait_for(exited, timeout=10)
            assert d.save_path('TMP').read_bytes() == latest
            report['fresh_enter_exits_preserving_disk'] = True
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(work, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', action='store_true')
    args = parser.parse_args()
    targets = ((lang, level) for lang in ('ru', 'en') for level in (1, 2, 3)) if args.matrix else (('ru', 1),)
    for language, level in targets:
        print(json.dumps(run(language, level), ensure_ascii=True), flush=True)
