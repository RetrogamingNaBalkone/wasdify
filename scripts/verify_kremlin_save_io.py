"""Exercise the KRS DPMI/DOS transport in isolated NE game copies.

This is a development harness, not the save-load patch. It installs no user
feature and never touches the reference executables. Outputs stay in mqa/.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
from zlib import crc32

from profile_kremlin import Api, ROOT
from verify_kremlin_settings_dosbox import wait_for, text
from wasdify.patches.kremlin_wasd import segments
from wasdify.patches.kremlin_weapons import WeaponSelectionFix
from wasdify.engine import build_result


MAGIC = b"WASDify-KRS-IO1!"
PROFILES = {
    "ru": ("RUS", "PKR.EXE", 39, 0x239,
           "12f78afc0eba05f76b5dc0bb99da38d7daa20585024c0ba958fe828aa3864d28"),
    "en": ("ENG", "PKE.EXE", 16, 0x18cf,
           "cce7bc5993d0734ad87e93db37a70b8b52faedfff14ac48d6e317e5ac996a37b"),
}


def records(items):
    return struct.pack("<H", len(items)) + b"".join(struct.pack("<BBHHH", *r) for r in items)


def validate_extra_hook(source, segment, offset, expected):
    if not source.startswith(expected, segment.start + offset):
        raise ValueError("Native death-check context differs")
    for kind, flags, cursor, _, _ in segment.records:
        width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
        if width is None:
            raise ValueError("Unknown relocation width")
        visited = set()
        while cursor != 0xffff:
            if cursor in visited or cursor + max(width, 2) > segment.end - segment.start:
                raise ValueError("Invalid relocation chain")
            visited.add(cursor)
            if cursor < offset + 5 and cursor + width > offset:
                raise ValueError("Relocation overlaps death hook")
            if flags & 4:
                break
            cursor = struct.unpack_from("<H", source, segment.start + cursor)[0]


def inject(source, payload, language, level=1, *, data_size=8192, pause_on_start=False):
    """Research-only NE installation, checked against exact versions AND code."""
    profile = PROFILES[language]
    autonomous = payload.count(b"KRSNAT01") == 1
    extra = 24 if autonomous else 16
    if data_size not in ((65536,) if autonomous else (8192, 12288)):
        raise ValueError("Unexpected research data-segment size")
    if hashlib.sha256(source).hexdigest() != profile[4]:
        raise ValueError("Unexpected reference version")
    all_segments = segments(source)
    ne = struct.unpack_from("<I", source, 60)[0]
    u16 = lambda offset: struct.unpack_from("<H", source, offset)[0]
    count = u16(ne + 28)
    if u16(ne + 34) != 64 or u16(ne + 50) != 9:
        raise ValueError("Unexpected NE layout")
    table_end = ne + 64 + count * 8
    contexts = json.loads((ROOT / "src/wasdify/data/kremlin_settings_profiles.json").read_text("ascii"))
    expected_tail = bytes.fromhex(contexts[language]["header_tail"])
    header_end = table_end + len(expected_tail)
    tail_end = min(s.start for s in all_segments)
    tail = source[table_end:header_end]
    if tail != expected_tail or header_end + extra > tail_end or any(source[header_end:header_end + extra]):
        raise ValueError("No validated space for NE segment entries")
    owner = next(s for s in all_segments if s.index == profile[2])
    site = profile[3]
    context = bytes.fromhex("8cd09045558bec1e8ed85657c45e0626833f00")
    if source[owner.start + site:owner.start + site + len(context)] != context:
        raise ValueError("Native clock-start context differs")
    # No original relocation, including any chained source, may touch the hook.
    for kind, flags, cursor, _, _ in owner.records:
        width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
        if width is None:
            raise ValueError("Unknown relocation width")
        seen = set()
        while cursor != 0xffff:
            if cursor in seen or cursor + max(width, 2) > owner.end - owner.start:
                raise ValueError("Invalid relocation chain")
            seen.add(cursor)
            if cursor < site + 5 and cursor + width > site:
                raise ValueError("Relocation overlaps hook")
            if flags & 4:
                break
            cursor = u16(owner.start + cursor)
    entry, fixup_start, fixup_count = struct.unpack_from("<3H", payload, 8)
    code_index, data_index = count + 1, count + 2
    code_records = []
    for i in range(fixup_count):
        offset, token = struct.unpack_from("<HH", payload, fixup_start + i * 4)
        if token == 0:
            code_records.append((2, 0, offset, data_index, 0))
        elif token == 1:
            code_records.append((3, 0, offset, owner.index, site + 5))
        elif token == 2:
            code_records.append((2, 0, offset, 2, 0))
        elif token == 3:
            clock_offset = 0x15d if language == "ru" else 0x17f3
            expected = bytes.fromhex("8cd09045558bec1e8ed883ec025657fa")
            if source[owner.start + clock_offset:owner.start + clock_offset + len(expected)] != expected:
                raise ValueError("Native PIT-clock context differs")
            code_records.append((3, 0, offset, owner.index, clock_offset))
        elif token == 13 and autonomous:
            code_records.append((2, 0, offset, count + 3, 0))
        elif token in (4, 5, 6, 7, 8, 9, 10, 11, 12) and language == "ru":
            segment_index, native_offset, expected = {
                4: (14, 0x2368, "8cd09045558bec1e8ed883ec105657688300"),
                5: (20, 0x0205, "8cd09045558bec1e8ed883ec0256578a460a"),
                6: (25, 0x1278, "8cd09045558bec1e8ed85657c45e0626834f1022"),
                7: (20, 0x0000, "8cd09045558bec1e8ed883ec02565766837e0600"),
                8: (20, 0x004f, "8cd09045558bec1e8ed883ec045657c45e06"),
                9: (14, 0x193e, "8cd09045558bec1e8ed883ec185657833ec60001"),
                10: (25, 0x155d, "8cd09045558bec1e8ed883ec08565766ff36da21"),
                11: (35, 0x0524, "8cd09045558bec1e8ed883ec0a5657680003"),
                12: (2, 0x0545, "8cd09045558bec1e8ed883ec0a565766ff760e"),
            }[token]
            native = next(s for s in all_segments if s.index == segment_index)
            if not source.startswith(bytes.fromhex(expected), native.start + native_offset):
                raise ValueError("Native instance-management context differs")
            code_records.append((3, 0, offset, segment_index, native_offset + (5 if token in (9, 12) else 0)))
        else:
            raise ValueError("Unknown payload fixup")
    result = bytearray(source)
    if level not in (1, 2, 3):
        raise ValueError("Invalid research level")
    if level != 1:
        main = next(s for s in all_segments if s.index == 2)
        pattern = bytes.fromhex("04308ad026884705")
        matches = [i for i in range(main.start, main.end - len(pattern) + 1)
                   if source.startswith(pattern, i)]
        if len(matches) != 1:
            raise ValueError("Ambiguous level-selection context")
        # Research only: choose menu entry 1 but load level 2/3 resources.
        # This is not the production mechanism for selecting or saving a level.
        result[matches[0] + 1] = 0x30 + level - 1
    result[owner.start + site:owner.start + site + 5] = b"\xea\xff\xff\0\0"
    end = owner.end + 2 + len(owner.records) * 8
    if any(source[end:end + 8]) or any(s.start < end + 8 and s.end > end
                                       for s in all_segments if s != owner):
        raise ValueError("No space for the hook relocation")
    result[owner.end:end + 8] = records(owner.records + ((3, 0, site + 1, code_index, entry),))
    result[table_end + extra:header_end + extra] = tail
    result[table_end:table_end + extra] = bytes(extra)
    struct.pack_into("<H", result, ne + 28, count + extra // 8)
    for offset in (4, 36, 38, 40, 42):
        struct.pack_into("<H", result, ne + offset, u16(ne + offset) + extra)
    struct.pack_into("<I", result, ne + 44, struct.unpack_from("<I", source, ne + 44)[0] + extra)
    live_manifest = payload.find(b"KRSLIVE1")
    if pause_on_start:
        if language != "ru" or live_manifest < 0:
            raise ValueError("Startup gate requires the RUS live-reload payload")
        main = next(s for s in all_segments if s.index == 2)
        intro_context = bytes.fromhex("837ec605754b66ff368cb49affff000083c404c746c60600")
        if not source.startswith(intro_context, main.start + 0x0f2f):
            raise ValueError("Native introduction/palette context differs")
        early_manifest = payload.find(b"KRSEARL1")
        if early_manifest < 0 or payload.count(b"KRSEARL1") != 1:
            raise ValueError("Missing early level-selection hook")
        early_entry, = struct.unpack_from("<H", payload, early_manifest + 8)
        if early_entry >= len(payload):
            raise ValueError("Invalid early hook entry")
        validate_extra_hook(source, main, 0x0545, bytes.fromhex("8cd09045558bec1e8ed883ec0a565766ff760e"))
        body = bytearray(result[main.start:main.end])
        body[0x0545:0x054a] = b"\xea\xff\xff\0\0"
        result.extend(bytes(-len(result) % 512))
        main_start = len(result)
        result.extend(body + records(main.records + ((3, 0, 0x0546, code_index, early_entry),)))
        main_entry = ne + 64 + (main.index - 1) * 8
        _, _, main_flags, main_minimum = struct.unpack_from("<4H", source, main_entry)
        struct.pack_into("<4H", result, main_entry, main_start >> 9, len(body), main_flags, main_minimum)
    if live_manifest >= 0:
        if language != "ru" or payload.count(b"KRSLIVE1") != 1:
            raise ValueError("Invalid live-reload manifest")
        death_entry, = struct.unpack_from("<H", payload, live_manifest + 8)
        if death_entry >= len(payload):
            raise ValueError("Invalid death hook entry")
        death = next(s for s in all_segments if s.index == 14)
        validate_extra_hook(source, death, 0x193e, bytes.fromhex("8cd09045558bec1e8ed883ec185657833ec60001"))
        body = bytearray(source[death.start:death.end])
        body[0x193e:0x1943] = b"\xea\xff\xff\0\0"
        result.extend(bytes(-len(result) % 512))
        death_start = len(result)
        result.extend(body + records(death.records + ((3, 0, 0x193f, code_index, death_entry),)))
        original_entry = ne + 64 + (death.index - 1) * 8
        _, _, death_flags, death_minimum = struct.unpack_from("<4H", source, original_entry)
        struct.pack_into("<4H", result, original_entry, death_start >> 9, len(body), death_flags, death_minimum)
    result.extend(bytes(-len(result) % 512))
    code_start = len(result)
    result.extend(payload + records(code_records))
    result.extend(bytes(-len(result) % 512))
    data_start = len(result)
    result.extend(MAGIC + bytes(data_size - len(MAGIC)))
    if pause_on_start:
        struct.pack_into("<H", result, data_start + 156, 1)
    if live_manifest >= 0:
        struct.pack_into("<H", result, data_start + 152, 1)  # validated live-reload ABI
    struct.pack_into("<4H", result, table_end, code_start >> 9, len(payload), 0x0d50, len(payload))
    struct.pack_into("<4H", result, table_end + 8, data_start >> 9, data_size & 0xffff, 0x0c51, data_size & 0xffff)
    if autonomous:
        result.extend(bytes(-len(result) % 512))
        snapshot_start = len(result)
        result.extend(bytes(65536))
        struct.pack_into("<4H", result, table_end + 16, snapshot_start >> 9, 0, 0x0c51, 0)
    return bytes(result)


def trial(language, nasm, level=1, observer=None, *, data_size=8192, world_research=False,
          interactive=False, prebuilt_payload=None, play_mode=False):
    folder, filename, _, _, expected_hash = PROFILES[language]
    reference = ROOT / "originals/games/pkr" / folder / filename
    source = reference.read_bytes()
    work = ROOT / "mqa" / ("save-io-" + language + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    game = work / folder
    shutil.copytree(reference.parent, game)
    payload_path = work / "io.bin"
    if world_research and language != "ru":
        raise ValueError("World reconstruction currently targets RUS only")
    assembly = "kremlin_save_world.asm" if world_research else "kremlin_save_io.asm"
    if prebuilt_payload is None:
        subprocess.run([str(nasm), "-f", "bin", "-I", str(ROOT / "src/wasdify/patches") + "/",
                        f"-DENG={int(language == 'en')}",
                        str(ROOT / "src/wasdify/patches" / assembly),
                        "-o", str(payload_path)], check=True)
    else:
        payload_path.write_bytes(prebuilt_payload.read_bytes())
    payload = payload_path.read_bytes()
    instrumented = inject(source, payload, language, level, data_size=data_size,
                          pause_on_start=play_mode)
    # The research exercise switches weapons repeatedly; use the existing real
    # fix rather than reproduce the original cursor bug while measuring saves.
    weapon_fix = WeaponSelectionFix().analyze({"main": instrumented})
    instrumented = build_result({"main": instrumented}, weapon_fix.changes)["main"]
    (game / filename).write_bytes(instrumented)
    port = 8134 if language == "ru" else 8135
    api = Api(port)
    try:
        api.get("dosbox/info")
    except OSError:
        pass
    else:
        raise RuntimeError("Research port already in use")
    config = work / "dosbox.conf"
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
webserver_enabled = on
webserver_bind_address = 127.0.0.1
webserver_port = {port}
[autoexec]
mount -u c
mount c "{game}"
c:
{filename} -r0
{'exit' if interactive else ''}
''', encoding="ascii")
    environment = dict(os.environ)
    if not interactive:
        environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    report = {"language": language, "level": level,
              "work": str(work), "data_size": data_size, "world_research": world_research,
              "transport_sha256": hashlib.sha256(payload).hexdigest(),
              "checks": []}
    with (work / "stdout.log").open("wb") as out, (work / "stderr.log").open("wb") as err:
        process = subprocess.Popen([str(ROOT / "mqa/dosbox/dosbox.exe"), "-conf", str(config), "-noconsole"],
                                   cwd=ROOT / "mqa/dosbox", env=environment, stdout=out, stderr=err,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        report["process_id"] = process.pid
        try:
            wait_for(lambda: api.get("dosbox/info"))
            def locate():
                memory = api.get("memory/0/0x2000000")
                positions, start = [], 0
                while True:
                    start = memory.find(MAGIC, start)
                    if start < 0:
                        break
                    positions.append(start)
                    start += 1
                return positions[0] if len(positions) == 1 else None
            # Resource/overlay copies can contain the signature before relocation.
            base = wait_for(locate)

            def call(operation, *, handle=0, data=None, length=0, a=b"", b=b""):
                api.put(str(base + 34), struct.pack("<HH", handle, length))
                api.put(str(base + 512), a + b"\0")
                api.put(str(base + 768), b + b"\0")
                if data is not None:
                    api.put(str(base + 1024), data)
                done = struct.unpack("<H", api.get(f"memory/{base + 18}/2"))[0]
                api.put(str(base + 16), struct.pack("<H", operation))
                def completed():
                    value = api.get(f"memory/{base + 18}/6")
                    return struct.unpack("<3H", value) if struct.unpack_from("<H", value)[0] != done else None
                state, result, error = wait_for(completed, timeout=15)
                report["checks"].append({"op": operation, "result": result, "error": error})
                return result, error

            if not play_mode:
                # The clock trampoline runs in menus too. No world need be created
                # to test the actual extender, real DOS segment and native caller ABI.
                assert call(1) == (0, 0)
                assert call(2, a=b"KRSIO.TMP")[1] == 0
                handle = report["checks"][-1]["result"]
                assert call(2, a=b"KRSIO.TMP")[1] != 0
                data = bytes(range(256)) * 16
                assert call(4, handle=handle, data=data, length=len(data)) == (len(data), 0)
                assert call(6, handle=handle)[1] == 0
                # Observe actual DOSBox lock support. Never infer it from successful I/O.
                report["lock"] = call(10, handle=handle)
                if report["lock"][1] == 0:
                    assert call(11, handle=handle)[1] == 0
                assert call(7, handle=handle)[1] == 0
                assert (game / "KRSIO.TMP").read_bytes() == data
                assert call(8, a=b"KRSIO.TMP", b=b"KRSIO.SAV")[1] == 0
                assert not (game / "KRSIO.TMP").exists()
                handle, error = call(3, a=b"KRSIO.SAV")
                assert error == 0
                assert call(5, handle=handle, length=4096) == (4096, 0)
                assert api.get(f"memory/{base + 1024}/4096") == data
                api.put(str(base + 60), struct.pack("<I", 0xffffffff))
                assert call(14, length=4096)[1] == 0
                checksum, = struct.unpack("<I", api.get(f"memory/{base + 60}/4"))
                assert checksum ^ 0xffffffff == crc32(data)
                assert call(5, handle=handle, length=1) == (0, 0)  # EOF
                assert call(7, handle=handle)[1] == 0
                assert call(9, a=b"KRSIO.SAV")[1] == 0
                assert call(9, a=b"KRSIO.SAV")[1] != 0
                # Portable level lock: exclusive creation prevents a second writer.
                # A stale lock must be handled explicitly, never silently removed.
                lock_handle, error = call(2, a=b"KRS1.LCK")
                assert error == 0
                assert call(2, a=b"KRS1.LCK")[1] == 80
                assert call(7, handle=lock_handle)[1] == 0
                assert call(9, a=b"KRS1.LCK")[1] == 0
                # Rename failure must leave both the protected generation and TMP
                # available; DOS rename is not an overwrite/replace primitive.
                keep, error = call(2, a=b"KEEP.SAV")
                assert error == 0
                assert call(4, handle=keep, data=b"old", length=3) == (3, 0)
                assert call(7, handle=keep)[1] == 0
                new, error = call(2, a=b"NEW.TMP")
                assert error == 0
                assert call(4, handle=new, data=b"new", length=3) == (3, 0)
                assert call(7, handle=new)[1] == 0
                assert call(8, a=b"NEW.TMP", b=b"KEEP.SAV")[1] != 0
                assert (game / "KEEP.SAV").read_bytes() == b"old"
                assert (game / "NEW.TMP").read_bytes() == b"new"
                assert call(4, handle=0xffff, data=b"bad", length=3)[1] == 6
                assert call(7, handle=0xffff)[1] == 6
                assert call(9, a=b"NEW.TMP")[1] == 0
                assert call(9, a=b"KEEP.SAV")[1] == 0
            if observer is not None:
                report["world"] = observer(api, base, language, level)
            if interactive and report.get("world", {}).get("user_closed"):
                report["status"] = "user_closed"
            else:
                if not play_mode:
                    assert call(12)[1] == 0
                    assert call(5, handle=handle, length=1)[1] != 0
                report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["error"] = str(exc)
            try:
                report["screen"] = text(api)
                report["cpu"] = json.loads(api.get("cpu/state"))
            except OSError:
                report["diagnostics"] = "DOSBox API disconnected"
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            report["reference_unchanged"] = hashlib.sha256(reference.read_bytes()).hexdigest() == expected_hash
            (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(work, flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=PROFILES)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    args = parser.parse_args()
    trial(args.language, args.nasm)
