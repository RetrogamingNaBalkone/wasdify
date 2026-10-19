"""Lossless horizontal texel-load optimization in the 16-bit NE renderer."""
from importlib.resources import files
import json
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from .kremlin_wasd import segments, fail


def profiles():
    return json.loads(files("wasdify").joinpath("data", "kremlin_render.json").read_text("utf-8"))["profiles"]


class RenderOptimization:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        all_segments = segments(data)
        candidates = []
        for profile in profiles():
            for segment in all_segments:
                if segment.flags & (1 | 0x2000):
                    continue
                for offset in find_matches(data, profile["signature"], start=segment.start, end=segment.end):
                    candidates.append((profile, segment, offset))
        if len(candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(candidates))
        profile, owner, base = candidates[0]
        # External calls and internal relocation offsets were researched at entry 0.
        if base != owner.start:
            fail()
        if (5, 1, 0x22, 1, 0x71) not in owner.records:
            fail()  # Retain the researched loader import for huge-pointer shifts.
        changes, states, ranges = [], [], []
        for region in profile["regions"]:
            offset = base + region["relative"]
            old, new = bytes.fromhex(region["expected"]), bytes.fromhex(region["replacement"])
            actual = data[offset:offset + len(old)]
            if actual not in (old, new):
                raise WasdifyError("unexpected_bytes", offset=offset)
            states.append(actual == new)
            changes.append(Change("main", offset, old, new))
            ranges.append((offset, offset + len(old)))
        if any(states) and not all(states):
            fail()
        for segment in all_segments:
            occupied = set()
            for kind, flags, source, target, target_offset in segment.records:
                width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
                if width is None:
                    fail()
                # No external entry may bypass the relocated texel load.
                if flags & 3 == 0 and target == owner.index and 0x8e <= target_offset < 0xd2:
                    fail()
                chain = set()
                cursor = source
                while cursor != 0xffff:
                    if cursor in chain or cursor + max(width, 2) > segment.end - segment.start:
                        fail()
                    chain.add(cursor)
                    absolute = segment.start + cursor
                    if any(absolute < end and absolute + width > start for start, end in ranges):
                        fail()
                    locations = set(range(cursor, cursor + width))
                    if occupied & locations:
                        fail()
                    occupied.update(locations)
                    if flags & 4:
                        break
                    cursor = struct.unpack_from("<H", data, absolute)[0]
        return PatchAnalysis(already_applied=True) if all(states) else PatchAnalysis(changes=tuple(changes))
