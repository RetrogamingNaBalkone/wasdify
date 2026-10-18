"""Explicit registry. No real game patch or game probe is implemented yet.

Register implementations by (game_id, patch_id). A game probe must establish
game identity from contextual signatures; a filename or MZ header is not enough.
"""
from typing import Callable, Mapping

from ..models import Patch

IMPLEMENTATIONS: dict[tuple[str, str], Patch] = {}
GAME_PROBES: dict[str, Callable[[Mapping[str, bytes]], bool]] = {}

