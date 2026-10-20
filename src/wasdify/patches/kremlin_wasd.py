"""Context-validated, in-place input patch for the two researched NE editions."""
from dataclasses import dataclass
from importlib.resources import files
import json
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from .kremlin_resolution import code_regions


@dataclass(frozen=True)
class Segment:
    index: int
    start: int
    end: int
    flags: int
    records: tuple[tuple[int, ...], ...]


def profiles():
    return json.loads(files("wasdify").joinpath("data", "kremlin_wasd.json").read_text("utf-8"))["profiles"]


def fail():
    raise WasdifyError("unsupported_executable")


def segments(data):
    code_regions(data)
    ne = struct.unpack_from("<I", data, 60)[0]
    table = ne + struct.unpack_from("<H", data, ne + 34)[0]
    shift = struct.unpack_from("<H", data, ne + 50)[0]
    result = []
    for index in range(struct.unpack_from("<H", data, ne + 28)[0]):
        sector, length, flags, _ = struct.unpack_from("<4H", data, table + index * 8)
        if not sector:
            continue
        start = sector << shift
        end = start + (length or 65536)
        records = ()
        if flags & 0x100:
            if end + 2 > len(data):
                fail()
            count = struct.unpack_from("<H", data, end)[0]
            if end + 2 + count * 8 > len(data):
                fail()
            records = tuple(struct.unpack_from("<BBHHH", data, end + 2 + i * 8) for i in range(count))
        result.append(Segment(index + 1, start, end, flags, records))
    for left in result:
        if any(left.end <= right.start < left.end + 2 + len(left.records) * 8
               for right in result if left.flags & 0x100 and left != right):
            fail()
    return tuple(result)


def locate(data, profile, all_segments):
    found = {}
    for name, anchor in profile["anchors"].items():
        matches = [(segment, offset) for segment in all_segments
                   if not segment.flags & (1 | 0x2000)
                   for offset in find_matches(data, anchor["signature"], start=segment.start, end=segment.end)]
        if len(matches) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(matches))
        segment, offset = matches[0]
        # Payloads use the researched intra-segment offsets, never file offsets.
        if offset - segment.start != anchor["offset"]:
            fail()
        found[name] = segment
    if len({segment.index for segment in found.values()}) != 4:
        fail()
    reader = found["reader"]
    if len(find_matches(data, profile["irq_signature"], start=reader.start, end=reader.end)) != 1:
        fail()
    player = found["player"]
    for context in profile.get("physics_callers", ()):
        offset = player.start + context["offset"]
        expected = bytes.fromhex(context["expected"])
        if offset + len(expected) > player.end or data[offset:offset + len(expected)] != expected:
            fail()
    return found


def analyze_profile(data, profile, found):
    regions = []
    states = []
    for region in profile["regions"]:
        segment = found[region["anchor"]]
        offset = segment.start + profile["anchors"][region["anchor"]]["offset"] + region["relative"]
        old, new = bytes.fromhex(region["expected"]), bytes.fromhex(region["replacement"])
        if len(old) != len(new) or not segment.start <= offset < offset + len(old) <= segment.end:
            fail()
        actual = data[offset:offset + len(old)]
        previous = tuple(bytes.fromhex(value) for value in region.get("previous_replacements", ()))
        if actual not in (old, new) + previous:
            raise WasdifyError("unexpected_bytes", offset=offset)
        states.append("current" if actual == new else "shared" if actual == old and actual in previous
                      else "previous" if actual in previous else "original")
        regions.append((segment, offset, old, new))
    if "original" in states and any(state in ("current", "previous") for state in states):
        fail()
    patched = "original" not in states
    upgrade = patched and any(state in ("previous", "shared") for state in states)
    changes = []
    if not patched:
        changes.extend(Change("main", offset, old, new) for _, offset, old, new in regions)
    elif upgrade:
        changes.extend(Change("main", offset, data[offset:offset + len(old)], new)
                       for (_, offset, old, new), state in zip(regions, states) if state in ("previous", "shared"))

    for name in ("input", "mouse", "player", "reader"):
        segment = found[name]
        removed = set(profile["removed"].get(name, ()))
        retained = []
        seen = set()
        for record in segment.records:
            kind, flags, source, target, target_offset = record
            if source in seen:
                fail()
            seen.add(source)
            if source in removed:
                expected_offset = profile["reader_offset"]
                if name == "input" and source in (profile["anchors"]["input"]["offset"] + 0x270,
                                                  profile["anchors"]["input"]["offset"] + 0x317):
                    expected_offset += 76  # Native edge-only reader, adjacent to held reader.
                if patched or record != (3, 0, source, found["reader"].index, expected_offset):
                    fail()
                if data[segment.start + source:segment.start + source + 4] != b"\xff\xff\x00\x00":
                    fail()
                continue
            retained.append(record)
            # No loader fixup may rewrite payload instructions. The two native
            # far pointers remain at their original source offsets and bytes.
            width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
            if width is None:
                fail()
            cursor = source
            chain = set()
            while cursor != 0xffff:
                if cursor in chain or cursor + max(width, 2) > segment.end - segment.start:
                    fail()
                chain.add(cursor)
                absolute = segment.start + cursor
                for owner, offset, old, new in regions:
                    if owner != segment:
                        continue
                    begin, end = max(absolute, offset), min(absolute + width, offset + len(old))
                    if begin < end and old[begin-offset:end-offset] != new[begin-offset:end-offset]:
                        fail()
                if flags & 4:  # Additive fixups do not form a source chain.
                    break
                cursor = struct.unpack_from("<H", data, absolute)[0]
        if patched:
            if seen & removed:
                fail()
        elif not removed <= seen:
            fail()
        if name == "input":
            expected = ((3, 0, profile["retained"]["reader"], found["reader"].index, profile["reader_offset"]),
                        (3, 0, profile["retained"]["mouse_init"], found["mouse"].index, 0))
            if any(record not in retained for record in expected):
                fail()
        if not patched and removed:
            size = 2 + len(segment.records) * 8
            replacement = struct.pack("<H", len(retained)) + b"".join(struct.pack("<BBHHH", *r) for r in retained)
            changes.append(Change("main", segment.end, data[segment.end:segment.end + size], replacement.ljust(size, b"\x00")))
    return PatchAnalysis(changes=tuple(changes), already_applied=patched and not upgrade)


class WasdControls:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        from .kremlin_settings import installed, restore_base
        if installed(data):
            data = restore_base(data)
        all_segments = segments(data)
        candidates = []
        for profile in profiles():
            try:
                found = locate(data, profile, all_segments)
            except WasdifyError:
                continue
            candidates.append((profile, found))
        if len(candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(candidates))
        return analyze_profile(data, *candidates[0])
