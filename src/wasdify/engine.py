"""Dependency resolution and validation of snapshot-relative edits."""
from typing import Mapping

from .models import Change, Game, WasdifyError


def order_patches(game: Game, selected: tuple[str, ...]) -> tuple[str, ...]:
    definitions = {patch.id: patch for patch in game.patches}
    if not selected:
        raise WasdifyError("no_patches")
    chosen = set(selected)
    for patch_id in selected:
        if patch_id not in definitions:
            raise WasdifyError("unknown_patch", patch=patch_id)
        patch = definitions[patch_id]
        for dependency in patch.dependencies:
            if dependency not in chosen:
                raise WasdifyError("missing_dependency", patch=patch_id, dependency=dependency)
        for conflict in patch.conflicts:
            if conflict in chosen:
                raise WasdifyError("patch_conflict", patch=patch_id, conflict=conflict)
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(patch_id: str) -> None:
        if patch_id in visiting:
            raise WasdifyError("dependency_cycle", patch=patch_id)
        if patch_id in visited:
            return
        visiting.add(patch_id)
        for dependency in definitions[patch_id].dependencies:
            visit(dependency)
        visiting.remove(patch_id)
        visited.add(patch_id)
        ordered.append(patch_id)

    for patch_id in dict.fromkeys(selected):
        visit(patch_id)
    return tuple(ordered)


def build_result(targets: Mapping[str, bytes], changes: tuple[Change, ...]) -> dict[str, bytes]:
    """All offsets refer to the original snapshot, including length-changing edits."""
    for change in changes:
        if change.role not in targets:
            raise WasdifyError("missing_role", role=change.role)
        data = targets[change.role]
        if (change.offset < 0 or change.offset + len(change.expected) > len(data)
                or (not change.expected and change.offset != len(data))
                or change.expected == change.replacement):
            raise WasdifyError("invalid_change", offset=change.offset)
        if data[change.offset:change.offset + len(change.expected)] != change.expected:
            raise WasdifyError("unexpected_bytes", offset=change.offset)
    result = dict(targets)
    for role, data in targets.items():
        edits = sorted((c for c in changes if c.role == role), key=lambda c: c.offset)
        for left, right in zip(edits, edits[1:]):
            if (left.offset + len(left.expected) > right.offset
                    or left.offset == right.offset):
                raise WasdifyError("overlapping_changes", offset=right.offset)
        for change in reversed(edits):
            data = data[:change.offset] + change.replacement + data[change.offset + len(change.expected):]
        result[role] = data
    return result

