"""Faults and process cuts in the real NASM payload, with invented bytes only."""
import argparse
import json
import struct
from zlib import crc32
from unicorn import UC_HOOK_CODE

from build_kremlin_save_native import ROOT, build
from verify_kremlin_save_native_synthetic import NativeMachine


class FaultMachine(NativeMachine):
    def __init__(self, payload, language='ru', level=1):
        self.trace = []
        self.fail_at = self.cut_at = self.short_at = None
        self.cut = False
        super().__init__(payload, language, level)
        at = payload.index(b'KRSFAIL1') + 8
        self.entries.update(zip(('available', 'candidate', 'rebuild', 'fatal', 'try_validate'),
                                struct.unpack_from('<5H', payload, at)))
        # Direct synthetic calls need observation only at native ABI stubs and
        # the fatal gate. CRC/scalar loops execute entirely in Unicorn's CPU.
        self.u.hook_del(self.code_hook)
        for begin, end in ((0x50200, 0x50200), (0x50500, 0x50500), (0x50900, 0x50900),
                (0x10000 + self.entries['fatal'], 0x10000 + self.entries['try_validate'] - 1)):
            self.u.hook_add(UC_HOOK_CODE, self.observe, begin=begin, end=end)
        self.trace.clear()

    def interrupt(self, u, number, data):
        dos = number == 0x31 and self.reg('AX') == 0x300
        event = None
        previous = self.failure
        if dos:
            operation = self.read16(0x2005c) >> 8
            event = {'call': len(self.trace) + 1, 'operation': hex(operation)}
            if event['call'] == self.fail_at:
                self.failure = 'dos'
            if event['call'] == self.short_at:
                self.failure = 'short_write'
        super().interrupt(u, number, data)
        self.failure = previous
        if event:
            event['error'] = bool(self.read16(0x20060) & 1)
            self.trace.append(event)
            if event['call'] == self.cut_at:
                self.cut = True
                u.emu_stop()

    def invoke(self, name, **kwargs):
        try:
            return super().invoke(name, **kwargs)
        except AssertionError:
            if not self.cut:
                raise
            return False


def repaired(raw):
    result = bytearray(raw)
    result[40:44] = bytes(4)
    struct.pack_into('<I', result, 40, crc32(result))
    return bytes(result)


def run(payload, language, level, *, exhaustive=False):
    def machine():
        return FaultMachine(payload, language, level)

    prefix = ('KRN' if language == 'ru' else 'KEN') + str(level)
    sav, prv, tmp = ((prefix + '.' + suffix).encode('ascii') for suffix in ('SAV', 'PRV', 'TMP'))
    seed = machine()
    assert seed.invoke('capture')
    old = seed.snapshot()
    seed.write16(0x7105e, 45)
    assert seed.invoke('capture')
    new = seed.snapshot()
    checks = 0

    def read_files(files, expected, source):
        m = machine()
        m.files = {name: bytearray(data) for name, data in files.items()}
        assert m.invoke('available'), (language, level, tuple(files))
        assert m.snapshot() == expected
        assert m.read16(0x201b2) == source
        assert not m.handles
        return m

    # CRC-valid structural/semantic corruption must fall back before mutation.
    bad_health = bytearray(old)
    # The first record is the invented player, whose health offset comes from
    # the same independently decoded scalar layout used by gameplay assertions.
    from verify_kremlin_save_native import records
    from build_kremlin_save_native import layout_fields
    at = 64 + 16384 + 16
    for name, (_, wire) in layout_fields(1, 2).items():
        if name == 'health':
            struct.pack_into('<h', bad_health, at, 0)
            break
        at += struct.calcsize('<' + wire)
    assert next(iter(records(repaired(bad_health)).values()))['fields']['health'] == 0
    bad_health = repaired(bad_health)
    variants = [b'', old[:20], old[:-1], old + b'extra', b'X' + old[1:], bad_health]
    for bad in variants:
        m = read_files({sav: bad, prv: old, tmp: new}, old, 1)
        assert m.read16(0x201b0) == 0  # final candidate has no read error
        assert m.read16(0x200ca) == 0 and not m.terminated
        checks += 1
    for files, expected, source in (({sav: old, prv: new, tmp: new}, old, 0),
            ({prv: old, tmp: new}, old, 1), ({tmp: new}, new, 2),
            ({sav: bad_health, prv: bad_health, tmp: new}, new, 2)):
        read_files(files, expected, source)
        checks += 1
    for files in ({}, {sav: bad_health, prv: b'bad', tmp: b'incomplete'}):
        m = machine()
        m.files = {name: bytearray(data) for name, data in files.items()}
        before = bytes(m.u.mem_read(0x70000, 0x3000))
        assert not m.invoke('available')
        assert bytes(m.u.mem_read(0x70000, 0x3000)) == before
        assert m.read16(0x200ca) == 0 and not m.terminated
        assert not m.handles
        checks += 1

    # A bad current record no longer prevents saving; the good PRV survives.
    for bad in variants:
        m = machine()
        m.install(new)
        m.files = {sav: bytearray(bad), prv: bytearray(old)}
        assert m.invoke('write')
        assert bytes(m.files[sav]) == new and bytes(m.files[prv]) == old
        assert tmp not in m.files and not m.handles
        checks += 1

    # Every failure keeps the last checked SAV; every cut can be read in a
    # fresh process. Cuts include first-save TMP with no older generation.
    totals = {}
    for scenario, initial in (('rotation', {sav: old, prv: old}), ('first_save', {})):
        baseline = machine()
        baseline.install(new)
        baseline.files = {name: bytearray(data) for name, data in initial.items()}
        assert baseline.invoke('write')
        trace = baseline.trace
        totals[scenario] = len(trace)
        selected = range(1, len(trace) + 1) if exhaustive else [
            i + 1 for i, item in enumerate(trace)
            if int(item['operation'], 16) in (0x5b, 0x68, 0x3e, 0x41, 0x56)]
        for call in selected:
            m = machine()
            m.install(new)
            m.files = {name: bytearray(data) for name, data in initial.items()}
            m.fail_at = call
            assert not m.invoke('write'), (scenario, call, trace[call - 1])
            if initial:
                assert any(bytes(m.files.get(name, b'')) == old for name in (sav, prv))
                read_files(m.files, old if bytes(m.files.get(sav, b'')) != new else new,
                           0 if sav in m.files else 1)
            elif any(bytes(m.files.get(name, b'')) == new for name in (sav, tmp)):
                read_files(m.files, new, 0 if sav in m.files else 2)
            assert not m.handles, (scenario, call, m.handles)
            checks += 1
        if exhaustive:
            for call, item in enumerate(trace, 1):
                if int(item['operation'], 16) != 0x40:
                    continue
                m = machine()
                m.install(new)
                m.files = {name: bytearray(data) for name, data in initial.items()}
                m.short_at = call  # disk full can short-write any block
                assert not m.invoke('write')
                assert not m.handles and tmp not in m.files
                if initial:
                    assert bytes(m.files[sav]) == old and bytes(m.files[prv]) == old
                checks += 1
            for call in range(1, len(trace) + 1):
                m = machine()
                m.install(new)
                m.files = {name: bytearray(data) for name, data in initial.items()}
                m.cut_at = call
                assert not m.invoke('write') and m.cut
                # OS releases handles when the interrupted process exits.
                fresh = machine()
                fresh.files = {name: bytearray(data) for name, data in m.files.items()}
                found = fresh.invoke('available')
                if initial or bytes(m.files.get(tmp, b'')) == new or sav in m.files:
                    assert found
                    assert fresh.snapshot() in (old, new)
                else:
                    assert not found
                assert not fresh.handles
                checks += 1

    # Stale/foreign TMP is preserved, with an actionable in-game notice.
    for data in (b'foreign', new):
        m = machine()
        m.files = {sav: bytearray(old), tmp: bytearray(data)}
        m.install(new)
        assert not m.invoke('write')
        assert m.read16(0x201b4) == 1
        assert bytes(m.files[sav]) == old and bytes(m.files[tmp]) == data
        checks += 1

    # Error after mutation holds the fatal screen until a fresh Enter. It
    # cannot return to a caller holding native pointers, or edit save files.
    m = machine()
    m.files[sav] = bytearray(old)
    m.install(b'X' + old[1:])
    m.write16(0x200ca, 1)
    m.confirm_fatal = False
    try:
        m.invoke('validate', max_instructions=3000)
    except AssertionError:
        pass
    assert m.read16(0x200fe) == 2 and not m.terminated
    assert bytes(m.files[sav]) == old
    # Release without a press cannot exit.
    m.u.mem_write(m.global_at(0x2171), b'\x02')
    m.u.emu_start(0x10000 + m.reg('IP'), 0x1ff00, count=1000)
    assert not m.terminated
    m.confirm_fatal = True
    m.u.emu_start(0x10000 + m.reg('IP'), 0x1ff00, count=1000)
    assert m.terminated and bytes(m.files[sav]) == old
    checks += 1
    return {'language': language, 'level': level, 'checks': checks,
            'exhaustive': exhaustive, 'successful_dos_calls': totals, 'status': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', action='store_true')
    args = parser.parse_args()
    results = []
    for language in (('ru', 'en') if args.matrix else ('ru',)):
        output = ROOT / f'build/save-research/native-failures-{language}.bin'
        build(ROOT / 'build/tools/nasm-2.16.03/nasm.exe', output, language=language, test_gates=True)
        for level in ((1, 2, 3) if args.matrix else (1,)):
            result = run(output.read_bytes(), language, level, exhaustive=language == 'ru' and level == 1)
            results.append(result)
            print(json.dumps(result), flush=True)
    print(json.dumps({'checks': sum(r['checks'] for r in results), 'status': 'passed'}))
