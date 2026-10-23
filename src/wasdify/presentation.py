"""Shared human-readable results; machine output uses stable field names."""
from dataclasses import fields, is_dataclass
from pathlib import Path
from collections.abc import Mapping

from .i18n import Translator
from .models import ApplyResult, Detection, Plan, PatchDefinition
from .service import Service


def to_wire(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_wire(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return str(value)
    return value


def detection_text(detection: Detection, service: Service, tr: Translator) -> str:
    title = (service.catalog.game(detection.game_id).title[tr.language]
             if detection.game_id else tr("unknown_version"))
    lines = [title, tr("detection") + ": " + tr("method_" + detection.method),
             detection.version_id or tr("unknown_version"),
             tr("sha256") + ": " + detection.sha256,
             tr("size") + ": " + str(detection.size)]
    if detection.method != "hash":
        lines.append(tr("unknown_hash"))
    return "\n".join(lines)


def plan_text(plan: Plan, service: Service, tr: Translator) -> str:
    lines = [detection_text(plan.detection, service, tr), "",
             tr("ready" if plan.ready else "blocked"), tr("changes", count=len(plan.changes))]
    definitions = {p.id: p for p in service.list_patches(plan.detection.game_id)} if plan.detection.game_id else {}
    titles = lambda ids: ", ".join(definitions[p].title[tr.language] for p in ids)
    if plan.installed:
        lines.append(tr("installed_patches", patches=titles(plan.installed)))
    for patch in definitions.values():
        for extension in patch.extensions:
            if extension.id in plan.active_extensions:
                lines.append(tr("active_extension", effect=extension.description[tr.language]))
    if plan.updated:
        lines.append(tr("updated_patches", patches=titles(plan.updated)))
    if ("save-load-settings" in plan.active_extensions and "game-settings" in plan.updated
            and any(f.action != "no_changes" for f in plan.files)):
        lines.append(tr("setup_save_updated"))
    for item in plan.files:
        lines.append(tr("resource_"+item.action, path=str(item.path)))
    if plan.already_applied:
        lines.append(tr("already_applied", patches=", ".join(plan.already_applied)))
    lines.extend(tr.issue(issue) for issue in plan.issues)
    return "\n".join(lines)


def patch_details(patch: PatchDefinition, definitions: Mapping[str, PatchDefinition],
                  tr: Translator) -> str:
    names = lambda ids: ", ".join(definitions[p].title[tr.language] for p in ids) or tr("none")
    lines = [patch.description[tr.language], tr("requires") + ": " + names(patch.dependencies)]
    for extension in patch.extensions:
        lines.append(tr("extends") + ": " + names((extension.target,)) + ". "
                     + extension.description[tr.language])
    if patch.conflicts:
        lines.append(tr("conflicts") + ": " + names(patch.conflicts))
    return "\n".join(lines)


def apply_text(result: ApplyResult, tr: Translator) -> str:
    lines = [tr(result.status), tr("sha256") + ": " + result.sha256_after]
    for item in result.files:
        lines.append(tr("resource_result", path=str(item.path), status=tr(item.status)))
        if item.backup:
            lines.append(tr("backup_saved", path=str(item.backup)))
    if result.backup:
        lines.extend([tr("backup_saved", path=result.backup), tr("backup_manual_restore")])
    return "\n".join(lines)

