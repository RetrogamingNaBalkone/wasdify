"""Scale the native FONT8 raster and its layout metrics with the video mode."""
from importlib.resources import files
import json
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from .kremlin_wasd import fail, segments


def profiles():
    return json.loads(files("wasdify").joinpath(
        "data", "kremlin_font_scaling.json").read_text("utf-8"))["profiles"]


class FontScaling:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        all_segments = segments(data)
        candidates = []
        for profile in profiles():
            anchor = profile["anchors"][0]
            for segment in all_segments:
                if segment.flags & (1 | 0x2000):
                    continue
                for match in find_matches(data, anchor["signature"],
                                          start=segment.start, end=segment.end):
                    candidates.append((profile, segment, match))
        if len(candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(candidates))
        profile, text, match = candidates[0]
        if match - text.start != profile["anchors"][0]["offset"]:
            fail()
        owners = {"text": text}
        for anchor in profile["anchors"]:
            matches = [(s, m) for s in all_segments
                       if bool(s.flags & 1) == (anchor["role"] == "data")
                       and not s.flags & 0x2000
                       for m in find_matches(data, anchor["signature"], start=s.start, end=s.end)]
            if len(matches) != 1:
                fail()
            owner, offset = matches[0]
            if offset-owner.start != anchor["offset"]:
                fail()
            if anchor["role"] in owners and owners[anchor["role"]] != owner:
                fail()
            owners[anchor["role"]] = owner
        # Segment indices are part of the researched loader-call contract.
        if any(owners[role].index != profile[role + "_index"]
               for role in ("text", "startup", "data")):
            fail()
        for fixup in profile["fixups"]:
            owner = owners[fixup["role"]]
            if tuple(fixup["record"]) not in owner.records:
                fail()
            for source, following in fixup["links"]:
                if source+2 > owner.end-owner.start or struct.unpack_from(
                        "<H", data, owner.start+source)[0] != following:
                    fail()
        regions, states = [], []
        for region in profile["regions"]:
            owner = owners[region["role"]]
            old, new = bytes.fromhex(region["expected"]), bytes.fromhex(region["replacement"])
            offset = owner.start + region["offset"]
            if len(old) != len(new) or offset + len(old) > owner.end:
                fail()
            actual = data[offset:offset + len(old)]
            if actual not in (old, new):
                raise WasdifyError("unexpected_bytes", offset=offset)
            states.append(actual == new)
            regions.append((owner, offset, old, new))
        if any(states) and not all(states):
            fail()
        # Validate every loader source chain and reject writes over the patch,
        # and far entries into the interior of either replaced routine.
        for segment in all_segments:
            occupied = set()
            for kind, flags, source, target, target_offset in segment.records:
                width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
                if width is None:
                    fail()
                if flags & 3 == 0 and any(
                        target == owner.index and offset-owner.start < target_offset
                        < offset-owner.start+len(old)
                        for owner, offset, old, new in regions):
                    fail()
                cursor, chain = source, set()
                while cursor != 0xffff:
                    if cursor in chain or cursor + max(width, 2) > segment.end-segment.start:
                        fail()
                    chain.add(cursor)
                    locations = set(range(cursor, cursor+width))
                    if occupied & locations:
                        fail()
                    occupied.update(locations)
                    absolute = segment.start + cursor
                    if any(absolute < offset+len(old) and offset < absolute+width
                           for owner, offset, old, new in regions):
                        fail()
                    if flags & 4:
                        break
                    cursor = struct.unpack_from("<H", data, absolute)[0]
        return (PatchAnalysis(already_applied=True) if all(states) else PatchAnalysis(
            changes=tuple(Change("main", offset, old, new) for _, offset, old, new in regions)))
