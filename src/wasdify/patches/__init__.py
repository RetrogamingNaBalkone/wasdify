"""Explicit registry of contextual game probes and real patch implementations."""
from typing import Callable, Mapping

from ..models import Patch
from .kremlin_resolution import Resolution640x400, probe as kremlin_probe

IMPLEMENTATIONS: dict[tuple[str, str], Patch] = {
    ("dungeons-of-kremlin", "resolution-640x400"): Resolution640x400(),
}
GAME_PROBES: dict[str, Callable[[Mapping[str, bytes]], bool]] = {
    "dungeons-of-kremlin": kremlin_probe,
}
