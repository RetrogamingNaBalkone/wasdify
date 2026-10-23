"""Shared application service for CLI and GUI."""
from pathlib import Path
from dataclasses import replace
from typing import Callable, Mapping

from .catalog import Catalog, load_catalog
from .engine import build_result, order_patches
from .models import (ApplyResult, Detection, Inspection, Issue, Patch, PatchDefinition, Plan, PlannedFile,
                     WasdifyError)
from .patches import GAME_PROBES, IMPLEMENTATIONS
from .storage import read_target, sha256, write_result, write_set
from .resources import destination, read_resource


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
                from .patches.kremlin_save_native import installed, restore_base
                try:
                    language = detect_language(restore_base(data) if installed(data) else data)
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

    def _installed(self, data: bytes, detection: Detection) -> tuple[str, ...]:
        if detection.game_id is None:
            return ()
        game = self.catalog.game(detection.game_id)
        found = set()
        base = data
        if game.id == "dungeons-of-kremlin":
            from .patches import kremlin_save_native as saves
            base, layers = saves.unwrap_layers(base)
            found.update(layers)
        for definition in game.patches:
            if definition.id in ("save-load", "game-settings") and game.id == "dungeons-of-kremlin":
                continue
            implementation = self.implementations.get((game.id, definition.id))
            if definition.implemented and implementation is not None:
                try:
                    analysis = implementation.analyze({"main": base})
                except WasdifyError:
                    continue
                if analysis.already_applied and not analysis.issues and not analysis.changes:
                    found.add(definition.id)
        return tuple(p.id for p in game.patches if p.id in found)

    def inspect(self, path: str | Path, game_id: str | None = None) -> Inspection:
        path = Path(path).absolute()
        data = read_target(path)
        detection = self._detect(data, path, game_id)
        return Inspection(detection, self._installed(data, detection))

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
        additional = []
        installed = ()
        active_extensions = ()
        updated = ()
        if detection.game_id is None:
            issues.append(Issue("unknown_file"))
        else:
            game = self.catalog.game(detection.game_id)
            try:
                installed = self._installed(data["main"], detection)
                ordered = order_patches(game, chosen, installed)
            except WasdifyError as error:
                issues.append(error.issue)
            effective = set(installed) | set(ordered)
            active_extensions = tuple(e.id for p in game.patches if p.id in effective
                                      for e in p.extensions if e.target in effective)
            definitions = {p.id: p for p in game.patches}
            grouped = None
            from .patches import kremlin_save_native as saves
            if (game.id == "dungeons-of-kremlin" and ordered
                    and effective.intersection(("save-load", "game-settings"))
                    and isinstance(self.implementations.get((game.id, "save-load")), saves.SaveLoad)
                    and all(definitions[p].implemented and (game.id,p) in self.implementations for p in ordered)):
                try:
                    grouped = saves.analyze_selection(data["main"], ordered, self.implementations, game.id)
                except WasdifyError as error:
                    issues.append(error.issue)
                    ordered_to_analyze = ()
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
                output = build_result(data, tuple(changes))["main"]
                if grouped is not None and changes:
                    _, before_layers = saves.unwrap_layers(data["main"])
                    _, after_layers = saves.unwrap_layers(output)
                    updated = tuple(p for p in installed if p not in chosen
                                    and p in before_layers and before_layers[p] != after_layers.get(p))
            except WasdifyError as error:
                issues.append(error.issue)
        if detection.game_id and not issues:
            definitions = {p.id: p for p in self.catalog.game(detection.game_id).patches}
            destinations = {}
            file_owners = tuple(dict.fromkeys(ordered + tuple(
                p for p in installed if p == "game-settings")))
            for patch_id in file_owners:
                declared = definitions[patch_id].files
                if any(f.language is not None for f in declared) and detection.language is None:
                    issues.append(Issue("resource_language_required"))
                    continue
                variant = ("save-load" if "save-load-settings" in active_extensions else "base")
                variants = [f for f in declared
                            if (f.language is None or f.language == detection.language)
                            and (f.variant is None or f.variant == variant)]
                if declared and not variants:
                    issues.append(Issue("resource_language_required"))
                for item in variants:
                    try:
                        if item.role not in paths:
                            raise WasdifyError("missing_role", role=item.role)
                        path = destination(paths[item.role], item.destination)
                        key = str(path).casefold()
                        if key in {str(p).casefold() for p in paths.values()}:
                            raise WasdifyError("resource_conflict", path=str(path))
                        payload = read_resource(item.source, item.sha256)
                        old = read_target(path) if path.exists() else None
                        planned = PlannedFile((patch_id,), item.source, path, item.sha256,
                                              sha256(old) if old is not None else None,
                                              "no_changes" if old == payload else "create" if old is None else "replace")
                        if key in destinations:
                            index = destinations[key]
                            previous = additional[index]
                            if previous.sha256 != planned.sha256:
                                raise WasdifyError("resource_conflict", path=str(path))
                            additional[index] = replace(previous, patches=previous.patches+(patch_id,))
                        else:
                            if any(path in f.path.parents or f.path in path.parents for f in additional):
                                raise WasdifyError("resource_conflict", path=str(path))
                            destinations[key] = len(additional)
                            additional.append(planned)
                    except WasdifyError as error:
                        issues.append(error.issue)
            if ("game-settings" in installed and "game-settings" not in chosen
                    and any(f.action != "no_changes" for f in additional)
                    and "game-settings" not in updated):
                updated += ("game-settings",)
        return Plan(paths, detection, chosen, ordered, tuple(changes), tuple(applied), tuple(issues),
                    tuple(additional), installed, active_extensions, updated)

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
        if (not fresh.ready or fresh.changes != plan.changes or fresh.ordered != plan.ordered
                or fresh.files != plan.files or fresh.installed != plan.installed
                or fresh.active_extensions != plan.active_extensions or fresh.updated != plan.updated):
            raise WasdifyError("plan_changed")
        output = build_result({"main": original}, fresh.changes)["main"]
        if not fresh.files:
            return write_result(path, plan.detection.sha256, output, backup=backup)
        entries = ((path, plan.detection.sha256, output),) + tuple(
            (f.path, f.sha256_before, read_resource(f.source, f.sha256)) for f in fresh.files)
        results = write_set(entries, backup=backup)
        main, *additional = results
        return ApplyResult("applied" if any(r.status == "applied" for r in results) else "no_changes",
                           plan.detection.sha256, main.sha256, main.backup, tuple(additional))

