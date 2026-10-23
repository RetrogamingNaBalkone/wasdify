"""Explicit registry of contextual game probes and real patch implementations."""
from typing import Callable, Mapping

from ..models import Patch
from .kremlin_resolution import Resolution640x400, probe as kremlin_probe
from .kremlin_wasd import WasdControls
from .kremlin_render import RenderOptimization
from .kremlin_numeric_hud import NumericHud
from .kremlin_font_scaling import FontScaling
from .kremlin_settings import GameSettings
from .kremlin_weapons import WeaponSelectionFix
from .kremlin_save_native import SaveLoad

IMPLEMENTATIONS: dict[tuple[str, str], Patch] = {
    ("dungeons-of-kremlin", "save-load"): SaveLoad(),
    ("dungeons-of-kremlin", "weapon-selection-fix"): WeaponSelectionFix(),
    ("dungeons-of-kremlin", "game-settings"): GameSettings(),
    ("dungeons-of-kremlin", "font-scaling"): FontScaling(),
    ("dungeons-of-kremlin", "numeric-hud"): NumericHud(),
    ("dungeons-of-kremlin", "render-optimization"): RenderOptimization(),
    ("dungeons-of-kremlin", "wasd"): WasdControls(),
    ("dungeons-of-kremlin", "resolution-640x400"): Resolution640x400(),
}
GAME_PROBES: dict[str, Callable[[Mapping[str, bytes]], bool]] = {
    "dungeons-of-kremlin": kremlin_probe,
}
