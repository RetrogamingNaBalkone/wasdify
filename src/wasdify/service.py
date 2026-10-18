"""Shared application service for CLI and GUI."""
from pathlib import Path
from typing import Callable, Mapping

from .catalog import Catalog, load_catalog
from .engine import build_result, order_patches
from .models import (ApplyResult, Detection, Issue, Patch, PatchDefinition, Plan,
                     WasdifyError)
from .patches import GAME_PROBES, IMPLEMENTATIONS
from .storage import read_target, sha256, write_result


class Service:
    def __init__(self, catalog: Catalog | None = None,
                 implementations: Mapping[tuple[str, str], Patch] | None = None,
                 probes: Mapping[str, Callable[[Mapping[str, bytes]], bool]] | None = None):
        self.catalog = catalog if catalog is not None else load_catalog()
        self.implementations = dict(IMPLEMENTATIONS if implementations is None else implementations)
        self.probes = dict(GAME_PROBES if probes is None else probes)

    def _detect(self, data: bytes, path: Path, game_id: str | None) -> Detection:
        digest = sha256(data)
        known = [(game, version) for game in self.catalog.games for version in game.versions
                 if version.sha256 == digest and version.size == len(data)]
        if len(known) > 1:
            raise WasdifyError("ambiguous_game")
        if game_id:
            self.catalog.game(game_id)
        if known:
            game, version = known[0]
            if game_id and game_id != game.id:
                raise WasdifyError("game_mismatch", game=game_id, detected=game.id)
            return Detection(digest, len(data), game.id, version.id, version.language, "hash")
        matches = [game.id for game in self.catalog.games
                   if game.id in self.probes and self.probes[game.id]({"main": data})]
        if len(matches) > 1:
            raise WasdifyError("ambiguous_game")
        if matches:
            if game_id and game_id != matches[0]:
                raise WasdifyError("game_mismatch", game=game_id, detected=matches[0])
            return Detection(digest, len(data), matches[0], method="signature")
        hints = tuple(game.id for game in self.catalog.games if any(
            version.filename.casefold() == path.name.casefold() for version in game.versions))
        return Detection(digest, len(data), game_id, method="manual" if game_id else "unknown", hints=hints)

    def detect(self, path: str | Path, game_id: str | None = None) -> Detection:
        path = Path(path).absolute()
        return self._detect(read_target(path), path, game_id)

    def list_patches(self, game_id: str) -> tuple[PatchDefinition, ...]:
        return self.catalog.game(game_id).patches

    def plan(self, targets: Mapping[str, str | Path], selected: tuple[str, ...],
             game_id: str | None = None) -> Plan:
        if len(targets) != 1:
            raise WasdifyError("single_file_only")
        if "main" not in targets:
            raise WasdifyError("missing_role", role="main")
        paths = {role: Path(path).absolute() for role, path in targets.items()}
        data = {role: read_target(path) for role, path in paths.items()}
        detection = self._detect(data["main"], paths["main"], game_id)
        chosen = tuple(dict.fromkeys(selected))
        issues: list[Issue] = []
        ordered: tuple[str, ...] = ()
        changes = []
        applied = []
        if detection.game_id is None:
            issues.append(Issue("unknown_file"))
        else:
            game = self.catalog.game(detection.game_id)
            try:
                ordered = order_patches(game, chosen)
            except WasdifyError as error:
                issues.append(error.issue)
            definitions = {p.id: p for p in game.patches}
            for patch_id in ordered:
                definition = definitions[patch_id]
                if not definition.implemented:
                    issues.append(Issue("not_implemented", {"patch": patch_id}))
                    continue
                missing = set(definition.roles) - data.keys()
                if missing:
                    issues.append(Issue("missing_role", {"role": sorted(missing)[0]}))
                    continue
                implementation = self.implementations.get((game.id, patch_id))
                if implementation is None:
                    issues.append(Issue("implementation_missing", {"patch": patch_id}))
                    continue
                try:
                    analysis = implementation.analyze(data)
                except WasdifyError as error:
                    issues.append(error.issue)
                    continue
                issues.extend(analysis.issues)
                if analysis.issues:
                    continue
                if (analysis.already_applied and analysis.changes
                        or not analysis.already_applied and not analysis.changes
                        or any(c.role not in definition.roles for c in analysis.changes)):
                    issues.append(Issue("invalid_analysis", {"patch": patch_id}))
                elif analysis.already_applied:
                    applied.append(patch_id)
                else:
                    changes.extend(analysis.changes)
            try:
                build_result(data, tuple(changes))
            except WasdifyError as error:
                issues.append(error.issue)
        return Plan(paths, detection, chosen, ordered, tuple(changes), tuple(applied), tuple(issues))

    def apply(self, plan: Plan, *, backup: bool = True) -> ApplyResult:
        if plan.issues:
            raise WasdifyError("plan_blocked")
        if len(plan.targets) != 1 or "main" not in plan.targets:
            raise WasdifyError("single_file_only")
        path = plan.targets["main"]
        original = read_target(path)
        if sha256(original) != plan.detection.sha256:
            raise WasdifyError("file_changed", path=str(path))
        # Re-analyze instead of trusting externally constructed/stale edit lists.
        fresh = self.plan(plan.targets, plan.selected, plan.detection.game_id)
        if not fresh.ready or fresh.changes != plan.changes or fresh.ordered != plan.ordered:
            raise WasdifyError("plan_changed")
        output = build_result({"main": original}, fresh.changes)["main"]
        return write_result(path, plan.detection.sha256, output, backup=backup)

