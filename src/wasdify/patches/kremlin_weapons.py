"""Avoid dereferencing the current weapon merely to recover its slot number."""
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from .kremlin_wasd import segments, fail

# Shared RUS/ENG selector, through the availability and animation-state checks.
CONTEXT = bytes.fromhex(
    "8cd09045558bec1e8ed856578b560ac45e06268b87ce00"
    "c1e00203d826c49faa00268b878100c45e06"
    "268987d000263997d0000f84b5008bc2c1e002c45e0603d8"
    "268b87aa00260b87ac000f849d008b5e06268997d000268b87ce00"
    "c1e00203d8268b87aa00260b87ac00745a8b5e06268b87ce00"
    "c1e00203d826c49faa0026f6878300047e65"
)
BEGIN, END = 23, 41
ORIGINAL = CONTEXT[BEGIN:END]
REPLACEMENT = b"\x90" * (END - BEGIN)
EPILOGUE = bytes.fromhex("5f5e1f5d4dcb")


class WeaponSelectionFix:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        candidates = []
        pattern = " ".join("??" if BEGIN <= i < END else f"{v:02x}"
                           for i, v in enumerate(CONTEXT))
        for segment in segments(data):
            if segment.flags & (1 | 0x2000):
                continue
            for offset in find_matches(data, pattern, start=segment.start, end=segment.end):
                candidates.append((segment, offset))
        if len(candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(candidates))
        owner, entry = candidates[0]
        if entry + 0xf2 > owner.end or data[entry+0xec:entry+0xf2] != EPILOGUE:
            fail()
        offset = entry + BEGIN
        actual = data[offset:entry+END]
        if actual not in (ORIGINAL, REPLACEMENT):
            raise WasdifyError("unexpected_bytes", offset=offset)
        # A loader fixup must never overwrite the replaced instructions.
        for kind, flags, source, _, _ in owner.records:
            width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
            if width is None:
                fail()
            seen = set()
            while source != 0xffff:
                if source in seen or source + max(width, 2) > owner.end-owner.start:
                    fail()
                seen.add(source)
                absolute = owner.start + source
                if absolute < entry+END and absolute+width > offset:
                    fail()
                if flags & 4:
                    break
                source = struct.unpack_from("<H", data, absolute)[0]
        if actual == REPLACEMENT:
            return PatchAnalysis(already_applied=True)
        return PatchAnalysis(changes=(Change("main", offset, ORIGINAL, REPLACEMENT),))
