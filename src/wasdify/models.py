"""UI-independent public contracts. All offsets are file offsets."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class Issue:
    code: str
    params: Mapping[str, object] = field(default_factory=dict)


class WasdifyError(Exception):
    def __init__(self, code: str, **params: object):
        self.issue = Issue(code, params)
        super().__init__(code)


@dataclass(frozen=True)
class KnownVersion:
    id: str
    sha256: str
    size: int
    language: str
    filename: str


@dataclass(frozen=True)
class PatchDefinition:
    id: str
    title: Mapping[str, str]
    description: Mapping[str, str]
    roles: tuple[str, ...] = ("main",)
    dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    implemented: bool = False


@dataclass(frozen=True)
class Game:
    id: str
    title: Mapping[str, str]
    roles: tuple[str, ...]
    versions: tuple[KnownVersion, ...]
    patches: tuple[PatchDefinition, ...]


@dataclass(frozen=True)
class Detection:
    sha256: str
    size: int
    game_id: str | None = None
    version_id: str | None = None
    language: str | None = None
    method: str = "unknown"
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Change:
    role: str
    offset: int
    expected: bytes
    replacement: bytes


@dataclass(frozen=True)
class PatchAnalysis:
    changes: tuple[Change, ...] = ()
    issues: tuple[Issue, ...] = ()
    already_applied: bool = False


class Patch(Protocol):
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        """Validate signatures/context and return changes or a blocking issue."""


@dataclass(frozen=True)
class Plan:
    targets: Mapping[str, Path]
    detection: Detection
    selected: tuple[str, ...]
    ordered: tuple[str, ...]
    changes: tuple[Change, ...] = ()
    already_applied: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class ApplyResult:
    status: str
    sha256_before: str
    sha256_after: str
    backup: Path | None = None


def exit_code(issues: tuple[Issue, ...]) -> int:
    if any(issue.code in {"io_error", "backup_failed", "write_failed", "gui_unavailable"}
           for issue in issues):
        return 4
    if any(issue.code == "not_implemented" for issue in issues):
        return 5
    return 3 if issues else 0

