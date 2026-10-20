"""Context-validated DOS configuration payload and shared selection planner."""
from importlib.resources import files
import json
import struct
from typing import Mapping

from ..engine import build_result
from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from . import kremlin_wasd as wasd

MAGIC = b"WASDSET-META-v1!!"


def resources():
    return json.loads(files("wasdify").joinpath("data/kremlin_settings.json").read_text("ascii"))


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _records(records):
    return struct.pack("<H", len(records)) + b"".join(struct.pack("<BBHHH", *r) for r in records)


def _profile(data):
    segments = wasd.segments(data)
    found = []
    for profile in wasd.profiles():
        try:
            located = wasd.locate(data, profile, segments)
            analysis = wasd.analyze_profile(data, profile, located)
            if not analysis.already_applied:
                continue
        except WasdifyError:
            continue
        found.append((profile, located))
    if len(found) != 1:
        wasd.fail()
    return found[0], segments


def _context(data, profile, segments):
    contexts = json.loads(files("wasdify").joinpath("data/kremlin_settings_profiles.json").read_text("ascii"))
    context = contexts[profile["language"]]
    by_index = {s.index: s for s in segments}
    for item in context["contexts"]:
        segment = by_index.get(item["segment"])
        if segment is None or segment.flags & (1 | 0x2000):
            wasd.fail()
        offset = segment.start + item["offset"]
        expected = bytes.fromhex(item["expected"])
        if offset+len(expected) > segment.end or data[offset:offset+len(expected)] != expected:
            wasd.fail()
    matches = [(s, o) for s in segments if not s.flags & (1 | 0x2000)
               for o in find_matches(data, context["main_pattern"], start=s.start, end=s.end)]
    if len(matches) != 1 or matches[0][0].index != 2:
        wasd.fail()
    return context, matches[0][0], matches[0][1]+15


def _install(data):
    (profile, located), segments = _profile(data)
    context, main, startup = _context(data, profile, segments)
    ne = struct.unpack_from("<I", data, 60)[0]
    count, table_relative, shift = _u16(data, ne+28), _u16(data, ne+34), _u16(data, ne+50)
    # This expansion is specific to the researched NE header layout. Generic
    # NE, spare MZ bytes, and a filename never establish compatibility.
    if (count != context["segments"] or table_relative != 64 or shift != 9
            or _u16(data,ne+14) != count or _u16(data,ne+26) != count
            or _u16(data,ne+22) != 1 or _u16(data,ne+20) != 0):
        wasd.fail()
    table_end = ne+64+count*8
    tail = bytes.fromhex(context["header_tail"])
    if data[table_end:table_end+len(tail)] != tail:
        wasd.fail()
    header_end = table_end+len(tail)
    first_segment = min(s.start for s in segments)
    if header_end+16 > first_segment or any(data[header_end:header_end+16]):
        wasd.fail()
    expected_offsets = {4: 64+count*8+24, 36: 64+count*8,
                        38: 64+count*8, 40: 64+count*8+7, 42: 64+count*8+11}
    if any(_u16(data, ne+k) != v for k,v in expected_offsets.items()):
        wasd.fail()
    if struct.unpack_from("<I", data, ne+44)[0] != table_end+26 or _u16(data, ne+32) != 11:
        wasd.fail()
    payloads = resources()
    code = bytes.fromhex(payloads["runtime"][profile["language"]])
    config = bytes.fromhex(payloads["data"])
    exports = struct.unpack_from("<8H", code, 8)
    code_index, data_index = count+1, count+2
    input_segment, mouse_segment = located["input"], located["mouse"]
    input_offset = profile["anchors"]["input"]["offset"]
    init_record = next((r for r in input_segment.records if r[2] == input_offset+0x2df), None)
    if (init_record is None or init_record[:2] != (3,0)
            or tuple(init_record[3:]) != tuple(context["joystick_init"])):
        wasd.fail()
    targets = ((data_index, 0), (1, 0x498a), (1, 0x0ab2), (1, 0x474a),
               (located["reader"].index, profile["reader_offset"]),
               (located["reader"].index, profile["reader_offset"]+76),
               (input_segment.index, input_offset-0xe71),
               (input_segment.index, input_offset-0xdc),
               (init_record[3], init_record[4]),
               (input_segment.index, input_offset+0x34d),
               (mouse_segment.index, 0x25e), (mouse_segment.index, 0))
    code_records = []
    for i in range(exports[7]):
        source, token = struct.unpack_from("<HH", code, exports[6]+i*4)
        target, offset = targets[token]
        if code[source:source+2] != b"\xff\xff":
            wasd.fail()
        code_records.append((2 if token == 0 else 3, 0, source, target, offset))
    result = bytearray(data)
    hooks, relocations = [], []
    # Native caller's prologue/epilogue and stack ownership remain intact.
    sites = ((main, startup-main.start, exports[0], 0x9a),
             (input_segment, input_offset+0x18, exports[1], 0xea),
             (mouse_segment, 0x174, exports[2], 0xea))
    additions = {}
    for segment, relative, entry, opcode in sites:
        absolute = segment.start+relative
        # Refuse any loader write into the new branch, including chained writes.
        for record in segment.records:
            cursor = record[2]
            seen = set()
            while cursor != 0xffff:
                if cursor in seen or cursor+4 > segment.end-segment.start:
                    wasd.fail()
                seen.add(cursor)
                width = {0:1,2:2,3:4,5:2,11:6,13:4}.get(record[0], 0)
                if not width or max(cursor, relative) < min(cursor+width, relative+5):
                    wasd.fail()
                if record[1] & 4:
                    break
                cursor = _u16(data, segment.start+cursor)
        old = data[absolute:absolute+5]
        new = bytes((opcode,))+b"\xff\xff\0\0"
        hooks.append([absolute, old.hex(), new.hex()])
        result[absolute:absolute+5] = new
        additions.setdefault(segment.index, []).append((3,0,relative+1,code_index,entry))
    for segment in segments:
        extra = additions.get(segment.index)
        if not extra:
            continue
        if not segment.flags & 0x100:
            wasd.fail()
        old_end = segment.end+2+len(segment.records)*8
        new_end = old_end+len(extra)*8
        occupied = [(s.start, s.end+2+len(s.records)*8 if s.flags&0x100 else s.end)
                    for s in segments if s.index != segment.index]
        if (new_end > len(data) or any(data[old_end:new_end])
                or any(max(old_end,a) < min(new_end,b) for a,b in occupied)):
            wasd.fail()
        result[segment.end:new_end] = _records(segment.records+tuple(extra))
        relocations.append([segment.index, list(extra[0])])
    header_old = data[ne:header_end+16]
    result[table_end+16:header_end+16] = tail
    result[table_end:table_end+16] = bytes(16)
    struct.pack_into("<H", result, ne+28, count+2)
    for k,v in expected_offsets.items():
        struct.pack_into("<H", result, ne+k, v+16)
    struct.pack_into("<I", result, ne+44, table_end+42)
    result.extend(bytes((-len(result))%512))
    code_start = len(result)
    result.extend(code+_records(code_records))
    result.extend(bytes((-len(result))%512))
    data_start = len(result)
    result.extend(config)
    struct.pack_into("<4H", result, table_end, code_start>>9, len(code), 0x0d50, len(code))
    struct.pack_into("<4H", result, table_end+8, data_start>>9, len(config), 0x0c51, len(config))
    manifest = {"language":profile["language"], "size":len(data), "ne":ne,
                "header":header_old.hex(), "hooks":hooks, "relocations":relocations}
    encoded = json.dumps(manifest, separators=(",",":"), sort_keys=True).encode("ascii")
    result.extend(encoded+struct.pack("<I",len(encoded))+MAGIC)
    return bytes(result)


def installed(data):
    return data.endswith(MAGIC)


def detect_language(data):
    """Language from independent contextual anchors, never from the filename."""
    if installed(data):
        data = restore_base(data)
    segments = wasd.segments(data)
    languages = []
    for profile in wasd.profiles():
        try:
            wasd.locate(data, profile, segments)
        except WasdifyError:
            continue
        languages.append(profile["language"])
    return languages[0] if len(languages)==1 else None


def restore_base(data):
    """Validate a complete installation by restoring and reproducing its edits."""
    try:
        if not installed(data):
            wasd.fail()
        length = struct.unpack_from("<I", data, len(data)-len(MAGIC)-4)[0]
        if not 1 <= length <= 16384:
            wasd.fail()
        end = len(data)-len(MAGIC)-4
        meta = json.loads(data[end-length:end].decode("ascii"))
        size, ne = meta["size"], meta["ne"]
        if not 64 <= size < end-length or ne != struct.unpack_from("<I", data,60)[0]:
            wasd.fail()
        header = bytes.fromhex(meta["header"])
        if not 64 <= len(header) <= 2048 or ne+len(header) > size:
            wasd.fail()
        base = bytearray(data[:size])
        if len(meta["hooks"]) != 3 or len(meta["relocations"]) != 3:
            wasd.fail()
        for offset, old, new in meta["hooks"]:
            original, replacement = bytes.fromhex(old), bytes.fromhex(new)
            if len(original) != 5 or len(replacement) != 5 or not 0 <= offset <= size-5:
                wasd.fail()
            if data[offset:offset+5] != replacement:
                wasd.fail()
            base[offset:offset+5] = original
        by_index = {s.index:s for s in wasd.segments(data)}
        for index, extra in meta["relocations"]:
            s = by_index[index]
            records = list(s.records)
            if records.count(tuple(extra)) != 1:
                wasd.fail()
            records.remove(tuple(extra))
            result = _records(records)+bytes(8)
            if s.end+len(result)>size:
                wasd.fail()
            base[s.end:s.end+len(result)] = result
        base[ne:ne+len(header)] = header
        baseline = bytes(base)
        if _install(baseline) != data:
            wasd.fail()
        return baseline
    except (KeyError, IndexError, TypeError, ValueError, struct.error, OverflowError, RecursionError) as error:
        raise WasdifyError("unsupported_executable") from error


def _diff(before, after, baseline=None, owners=None):
    changes = {}
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        owner = owners[i] if baseline is not None and after[i] == baseline[i] else "game-settings"
        start = i
        i += 1
        while (i < len(before) and before[i] != after[i]
               and (owners[i] if baseline is not None and after[i] == baseline[i] else "game-settings") == owner):
            i += 1
        changes.setdefault(owner, []).append(Change("main",start,before[start:i],after[start:i]))
    if len(after) > len(before):
        changes.setdefault("game-settings", []).append(Change("main",len(before),b"",after[len(before):]))
    return changes


class GameSettings:
    def analyze(self, targets: Mapping[str,bytes]) -> PatchAnalysis:
        data = targets["main"]
        if installed(data):
            restore_base(data)
            return PatchAnalysis(already_applied=True)
        output = _install(data)
        changes = _diff(data, output)
        return PatchAnalysis(changes=tuple(changes["game-settings"]))


def analyze_selection(data, ordered, implementations, game_id):
    """Compose in memory; emit disjoint edits against the one original snapshot.

    Native relocation-table changes from HUD/font/render are incorporated before
    adding the new fixups. No overlap is silently accepted by the engine.
    """
    if installed(data):
        restore_base(data)
        return {p:implementations[(game_id,p)].analyze({"main":data}) for p in ordered}
    analyses = {p:implementations[(game_id,p)].analyze({"main":data})
                for p in ordered if p != "game-settings"}
    if any(a.issues for a in analyses.values()):
        return {**analyses, "game-settings":PatchAnalysis(issues=tuple(
            issue for a in analyses.values() for issue in a.issues))}
    edits = tuple(c for a in analyses.values() for c in a.changes)
    baseline = build_result({"main":data}, edits)["main"]
    output = _install(baseline)
    owners = ["game-settings"]*len(data)
    for patch, analysis in analyses.items():
        for change in analysis.changes:
            owners[change.offset:change.offset+len(change.expected)] = [patch]*len(change.expected)
    partition = _diff(data,output,baseline,owners)
    return {p:PatchAnalysis(changes=tuple(partition.get(p,())),
                            already_applied=p not in partition) for p in ordered}
