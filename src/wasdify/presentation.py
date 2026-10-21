"""Shared human-readable results; machine output uses stable field names."""
from dataclasses import fields, is_dataclass
from pathlib import Path
from collections.abc import Mapping

from .i18n import Translator
from .models import ApplyResult, Detection, Plan
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
    for item in plan.files:
        lines.append(tr("resource_"+item.action, path=str(item.path)))
    if plan.already_applied:
        lines.append(tr("already_applied", patches=", ".join(plan.already_applied)))
    lines.extend(tr.issue(issue) for issue in plan.issues)
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

