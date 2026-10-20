"""Shared application service for CLI and GUI."""
from pathlib import Path
import os
from typing import Callable, Mapping

from .catalog import Catalog, load_catalog
from .engine import build_result, order_patches
from .models import (ApplyResult, Detection, Issue, Patch, PatchDefinition, Plan, SetupExportResult,
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
            language = None
            if matches[0] == "dungeons-of-kremlin":
                from .patches.kremlin_settings import detect_language
                try:
                    language = detect_language(data)
                except WasdifyError:
                    pass
            return Detection(digest, len(data), matches[0], language=language, method="signature")
        hints = tuple(game.id for game in self.catalog.games if any(
            version.filename.casefold() == path.name.casefold() for version in game.versions))
        return Detection(digest, len(data), game_id, method="manual" if game_id else "unknown", hints=hints)

    def detect(self, path: str | Path, game_id: str | None = None) -> Detection:
        path = Path(path).absolute()
        return self._detect(read_target(path), path, game_id)

    def list_patches(self, game_id: str) -> tuple[PatchDefinition, ...]:
        return self.catalog.game(game_id).patches

    def export_setup(self, directory: str | Path, language: str,
                     overwrite: bool = False) -> SetupExportResult:
        """Export the selected DOS language, independently of the frontend locale."""
        from .patches.kremlin_settings import resources
        if language not in ("ru", "en"):
            raise WasdifyError("setup_language_required")
        path = Path(directory).absolute() / "SETUP.EXE"
        payload = bytes.fromhex(resources()["setup"][language])
        digest = sha256(payload)
        if path.exists() or path.is_symlink():
            current = read_target(path)
            if current == payload:
                return SetupExportResult("no_changes", path, language, digest)
            if not overwrite:
                raise WasdifyError("setup_exists", path=str(path))
            result = write_result(path, sha256(current), payload, backup=True)
            return SetupExportResult(result.status, path, language, digest, result.backup)
        created = False
        try:
            with path.open("xb") as output:
                created = True
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if read_target(path) != payload:
                raise OSError("setup verification failed")
        except OSError as error:
            if created:
                path.unlink(missing_ok=True)
            raise WasdifyError("write_failed", path=str(path)) from error
        return SetupExportResult("exported", path, language, digest)

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
            grouped = None
            if "game-settings" in ordered:
                from .patches.kremlin_settings import GameSettings, analyze_selection
                if (isinstance(self.implementations.get((game.id, "game-settings")), GameSettings)
                        and all(definitions[p].implemented and (game.id,p) in self.implementations for p in ordered)):
                    try:
                        grouped = analyze_selection(data["main"], ordered, self.implementations, game.id)
                    except WasdifyError as error:
                        issues.append(error.issue)
                        ordered_to_analyze = ()
                    else:
                        ordered_to_analyze = ordered
                else:
                    ordered_to_analyze = ordered
            else:
                ordered_to_analyze = ordered
            for patch_id in ordered_to_analyze:
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
                    analysis = grouped[patch_id] if grouped is not None else implementation.analyze(data)
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

