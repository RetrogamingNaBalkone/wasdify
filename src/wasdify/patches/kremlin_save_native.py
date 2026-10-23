"""Autonomous save/load installation with contextual NE validation.

Original MIT payloads only; no assembler, game images or research API at runtime.
All additions are composed in memory before Service's ordinary transaction.
"""
from hashlib import sha256
from importlib.resources import files
import json
import struct
from typing import Mapping

from ..engine import build_result
from ..models import Change, PatchAnalysis, WasdifyError
from . import kremlin_settings as settings
from .kremlin_wasd import segments, fail, locate, profiles

MAGIC = b"WASD-SAVE-META1!"
DATA_MAGIC = b"WASDify-KRS-IO1!"


def resources():
    return json.loads(files("wasdify").joinpath("data/kremlin_save_native.json").read_text("ascii"))


def installed(data):
    return data.endswith(MAGIC)


def present(data):
    """A recognizable payload with a damaged footer must also fail closed."""
    if installed(data):
        return True
    try:
        return any(data[s.start:s.start+8] == b"KRSIO001" for s in segments(data))
    except WasdifyError:
        return False


def _diff(before, after):
    if len(after) < len(before):
        fail()
    changes = []
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < len(before) and before[i] != after[i]:
            i += 1
        changes.append(Change("main", start, before[start:i], after[start:i]))
    if len(after) > len(before):
        changes.append(Change("main", len(before), b"", after[len(before):]))
    return tuple(changes)


def _hook_context(data, segment, offset, expected):
    if (segment.flags & (1 | 0x2000) or offset + len(expected) > segment.end-segment.start
            or data[segment.start+offset:segment.start+offset+len(expected)] != expected):
        fail()
    # Check every chained source, including fixups that start before the hook.
    for kind, flags, cursor, _, _ in segment.records:
        width = {0:1, 2:2, 3:4, 5:2, 11:6, 13:4}.get(kind)
        seen = set()
        while cursor != 0xffff:
            if not width or cursor in seen or cursor+max(width, 2) > segment.end-segment.start:
                fail()
            seen.add(cursor)
            if max(cursor, offset) < min(cursor+width, offset+5):
                fail()
            if flags & 4:
                break
            cursor = settings._u16(data, segment.start+cursor)


def _install(source):
    try:
        return _install_validated(source)
    except (KeyError,IndexError,ValueError,TypeError,struct.error,OverflowError,StopIteration) as error:
        raise WasdifyError("unsupported_executable") from error


def _install_validated(source):
    language = settings.detect_language(source)
    if language not in ("ru", "en"):
        fail()
    all_segments = segments(source)
    profile = next(p for p in profiles() if p["language"] == language)
    locate(source, profile, all_segments)
    by_index = {s.index:s for s in all_segments}
    resource = resources()
    bindings = {int(k):v for k,v in resource["bindings"][language].items()}
    payload = bytes.fromhex(resource["runtime"][language])
    context = json.loads(files("wasdify").joinpath("data/kremlin_settings_profiles.json").read_text("ascii"))[language]
    ne = struct.unpack_from("<I", source, 60)[0]
    u16 = lambda offset: settings._u16(source, offset)
    count = u16(ne+28)
    has_settings = settings.installed(source)
    if has_settings:
        baseline = settings.restore_base(source)
        # Service migrates previous settings before reaching this layer.
        # A standalone analyzer must not bind to the old 19-action layout.
        if settings._install(baseline) != source:
            fail()
    expected_count = context["segments"] + (2 if has_settings else 0)
    if (count != expected_count or u16(ne+34) != 64 or u16(ne+50) != 9
            or u16(ne+14) != context["segments"] or u16(ne+26) != context["segments"]
            or u16(ne+22) != 1 or u16(ne+20) != 0 or u16(ne+32) != 11):
        fail()
    table_end = ne+64+count*8
    tail = bytes.fromhex(context["header_tail"])
    header_end = table_end+len(tail)
    offsets = {4:64+count*8+24,36:64+count*8,38:64+count*8,40:64+count*8+7,42:64+count*8+11}
    if (source[table_end:header_end] != tail or header_end+24 > min(s.start for s in all_segments)
            or any(source[header_end:header_end+24])
            or any(u16(ne+k) != v for k,v in offsets.items())
            or struct.unpack_from("<I",source,ne+44)[0] != table_end+26):
        fail()
    owner = by_index[39 if language == "ru" else 16]
    site = 0x239 if language == "ru" else 0x18cf
    _hook_context(source, owner, site, bytes.fromhex("8cd09045558bec1e8ed85657c45e0626833f00"))
    targets = {0:(count+2,0),1:(owner.index,site+5),2:(2,0),13:(count+3,0)}
    clock = 0x15d if language == "ru" else 0x17f3
    _hook_context(source,owner,clock,bytes.fromhex("8cd09045558bec1e8ed883ec025657fa"))
    targets[3] = (owner.index, clock)
    for token, (index, offset, expected) in bindings.items():
        native = by_index[index]
        # Every imported function has an independently researched context.
        _hook_context(source,native,offset,bytes.fromhex(expected))
        targets[token] = (index,offset+(5 if token in (9,12) else 0))
    entry, fixup_start, fixup_count = struct.unpack_from("<3H",payload,8)
    code_records = []
    for i in range(fixup_count):
        offset, token = struct.unpack_from("<HH",payload,fixup_start+i*4)
        if token not in targets or payload[offset:offset+2] != b"\xff\xff":
            fail()
        target, native_offset = targets[token]
        code_records.append((2 if token in (0,2,13) else 3,0,offset,target,native_offset))
    cfg = payload.index(b"KRSCFG01")+8
    if has_settings:
        payload = payload[:cfg]+b"\xff\xff"+payload[cfg+2:]
        code_records.append((2,0,cfg,count,0))
    result = bytearray(source)
    result[table_end+24:header_end+24] = tail
    result[table_end:table_end+24] = bytes(24)
    struct.pack_into("<H",result,ne+28,count+3)
    for k,v in offsets.items():
        struct.pack_into("<H",result,ne+k,v+24)
    struct.pack_into("<I",result,ne+44,table_end+50)

    def append_segment(body, records=()):
        result.extend(bytes(-len(result)%512))
        start = len(result)
        result.extend(body)
        if records:
            result.extend(settings._records(records))
        if start>>9 > 0xffff:
            fail()
        return start

    # Relocate whole hook-owning segments instead of consuming unknown padding
    # after relocation tables. Existing records and patched bodies survive.
    early = struct.unpack_from("<H",payload,payload.index(b"KRSEARL1")+8)[0]
    death = struct.unpack_from("<H",payload,payload.index(b"KRSLIVE1")+8)[0]
    hooks = ((owner,site,entry),(by_index[2],bindings[12][1],early),
             (by_index[bindings[9][0]],bindings[9][1],death))
    for segment,offset,target in hooks:
        if target >= len(payload):
            fail()
        body = bytearray(source[segment.start:segment.end])
        body[offset:offset+5] = b"\xea\xff\xff\0\0"
        records = segment.records+((3,0,offset+1,count+1,target),)
        start = append_segment(body, records)
        struct.pack_into("<H",result,ne+64+(segment.index-1)*8,start>>9)
    code_start = append_segment(payload,code_records)
    data = bytearray(65536)
    data[:len(DATA_MAGIC)] = DATA_MAGIC
    struct.pack_into("<H",data,152,1)
    struct.pack_into("<H",data,156,1)
    data_start = append_segment(data)
    snapshot_start = append_segment(bytes(65536))
    for slot,start,length,flags in ((0,code_start,len(payload),0x0d50),
                                   (1,data_start,0,0x0c51),(2,snapshot_start,0,0x0c51)):
        struct.pack_into("<4H",result,table_end+slot*8,start>>9,length,flags,length)
    edits = [[c.offset,c.expected.hex()] for c in _diff(source,bytes(result)) if c.expected]
    meta = json.dumps({"size":len(source),"sha256":sha256(source).hexdigest(),"edits":edits},
                      sort_keys=True,separators=(",",":")).encode("ascii")
    result.extend(meta+struct.pack("<I",len(meta))+MAGIC)
    segments(bytes(result))
    return bytes(result)


def restore_base(data):
    """Treat the manifest as untrusted; reproduce the complete installation."""
    try:
        if not installed(data):
            fail()
        end = len(data)-len(MAGIC)-4
        length = struct.unpack_from("<I",data,end)[0]
        if not 1 <= length <= 32768:
            fail()
        meta = json.loads(data[end-length:end].decode("ascii"))
        size = meta["size"]
        if not 64 <= size < end-length:
            fail()
        base = bytearray(data[:size])
        occupied = set()
        for offset,old in meta["edits"]:
            original = bytes.fromhex(old)
            if not original or not 0 <= offset <= size-len(original):
                fail()
            span = set(range(offset,offset+len(original)))
            if span & occupied:
                fail()
            occupied.update(span)
            base[offset:offset+len(original)] = original
        base = bytes(base)
        if sha256(base).hexdigest() != meta["sha256"] or _install(base) != data:
            fail()
        return base
    except (KeyError,IndexError,ValueError,TypeError,struct.error,OverflowError,RecursionError) as error:
        raise WasdifyError("unsupported_executable") from error


class SaveLoad:
    def analyze(self, targets: Mapping[str,bytes]) -> PatchAnalysis:
        data = targets["main"]
        if installed(data):
            restore_base(data)
            return PatchAnalysis(already_applied=True)
        return PatchAnalysis(changes=_diff(data,_install(data)))


def analyze_selection(data, ordered, implementations, game_id):
    """Unwrap validated layers, compose simple patches, rebuild preserved layers.

    Adding a patch after save/settings must update relocated copies and replay
    manifests too. Analyses and final nonoverlapping edits share one snapshot.
    """
    save_present = present(data)
    base = restore_base(data) if save_present else data
    settings_present = settings.installed(base)
    if settings_present:
        base = settings.restore_base(base)
    simple = {p:implementations[(game_id,p)].analyze({"main":base})
              for p in ordered if p not in ("save-load","game-settings")}
    if any(a.issues for a in simple.values()):
        issues = tuple(i for a in simple.values() for i in a.issues)
        return {p:PatchAnalysis(issues=issues) for p in ordered}
    baseline = build_result({"main":base},tuple(c for a in simple.values() for c in a.changes))["main"]
    output = baseline
    if settings_present or "game-settings" in ordered:
        output = settings._install(output)
    if save_present or "save-load" in ordered:
        output = _install(output)
    edits = _diff(data,output)
    # Each rebuilt container belongs to its selected feature, or to the feature
    # that caused the rebuild when that container was already installed.
    fallback = next((p for p in ("save-load","game-settings") if p in ordered), ordered[0])
    partition = {p:[] for p in ordered}
    for change in edits:
        owner = next((p for p,a in simple.items() for c in a.changes
                      if c.offset <= change.offset < c.offset+len(c.expected)), fallback)
        partition[owner].append(change)
    return {p:PatchAnalysis(changes=tuple(partition[p]),already_applied=not partition[p]) for p in ordered}
