"""Exact current life/magic values using the game's native HUD update path."""
from importlib.resources import files
import json
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from .kremlin_wasd import fail, segments


def profiles():
    return json.loads(files("wasdify").joinpath(
        "data", "kremlin_numeric_hud.json").read_text("utf-8"))["profiles"]


class NumericHud:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        all_segments = segments(data)
        candidates = []
        for profile in profiles():
            anchor = profile["anchors"][0]
            for owner in all_segments:
                if owner.flags & (1 | 0x2000):
                    continue
                for match in find_matches(data, anchor["expected"], start=owner.start, end=owner.end):
                    candidates.append((profile, owner, match))
        if len(candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(candidates))
        profile, owner, match = candidates[0]
        if match - owner.start != profile["anchors"][0]["offset"]:
            fail()
        for anchor in profile["anchors"]:
            expected = bytes.fromhex(anchor["expected"])
            absolute = owner.start + anchor["offset"]
            if absolute + len(expected) > owner.end or data[absolute:absolute + len(expected)] != expected:
                fail()
        # The existing relocated message printf pointer is reused by the payload.
        text_matches = [(segment, match) for segment in all_segments
                        if not segment.flags & (1 | 0x2000)
                        for match in find_matches(data, profile["text_signature"],
                                                  start=segment.start, end=segment.end)]
        if len(text_matches) != 1:
            fail()
        text, entry = text_matches[0]
        if entry - text.start != profile["text_offset"]:
            fail()
        if (3, 0, profile["print_source"], text.index, profile["print_target"]) not in owner.records:
            fail()
        if any(tuple(record) not in owner.records for record in profile["fixups"]):
            fail()
        pixel = profile["pixel"]
        pixel_matches = [(s, m) for s in all_segments if not s.flags & (1 | 0x2000)
                         for m in find_matches(data, pixel["signature"], start=s.start, end=s.end)]
        if len(pixel_matches) != 1:
            fail()
        segment, entry = pixel_matches[0]
        if entry-segment.start != pixel["offset"] or tuple(pixel["fixup"]) not in segment.records:
            fail()
        offset = owner.start + profile["offset"]
        old, new = bytes.fromhex(profile["expected"]), bytes.fromhex(profile["replacement"])
        if offset + len(old) > owner.end:
            fail()
        regions = [(offset, old, new)] + [
            (owner.start + r["offset"], bytes.fromhex(r["expected"]), bytes.fromhex(r["replacement"]))
            for r in profile["regions"]]
        changes, states = [], []
        for i, (begin, expected, replacement) in enumerate(regions):
            actual = data[begin:begin + len(expected)]
            previous = bytes.fromhex(profile["previous_replacement"]) if i == 0 else expected
            if begin + len(expected) > owner.end or actual not in (expected, replacement, previous):
                raise WasdifyError("unexpected_bytes", offset=begin)
            states.append(actual == replacement)
            changes.append(Change("main", begin, actual, replacement))
        if any(states) and not all(states):
            fail()
        # Preserve every fixup byte, including source-chain links. Reject loader
        # writes to new instructions and external entries into the old method.
        for segment in all_segments:
            occupied = set()
            for kind, flags, source, target, target_offset in segment.records:
                width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
                if width is None:
                    fail()
                if flags & 3 == 0 and target == owner.index and any(
                        begin-owner.start < target_offset < begin-owner.start+len(expected)
                        for begin, expected, replacement in regions):
                    fail()
                cursor, chain = source, set()
                while cursor != 0xffff:
                    if cursor in chain or cursor + max(width, 2) > segment.end - segment.start:
                        fail()
                    chain.add(cursor)
                    locations = set(range(cursor, cursor + width))
                    if occupied & locations:
                        fail()
                    occupied.update(locations)
                    absolute = segment.start + cursor
                    for start, expected, replacement in regions:
                        begin, end = max(absolute, start), min(absolute + width, start + len(expected))
                        if begin < end and expected[begin-start:end-start] != replacement[begin-start:end-start]:
                            fail()
                    if flags & 4:
                        break
                    cursor = struct.unpack_from("<H", data, absolute)[0]
        return (PatchAnalysis(already_applied=True) if all(states) else
                PatchAnalysis(changes=tuple(changes)))
