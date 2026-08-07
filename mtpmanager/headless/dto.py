"""JSON-serializable results and process exit codes for headless / CLI / MCP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any


class ExitCode(IntEnum):
    """Stable process exit codes for agents."""

    OK = 0
    ERROR = 1
    USAGE = 2
    DEVICE_BUSY = 3
    TRANSPORT_FATAL = 4
    NOT_FOUND = 5
    CONFIRM_REQUIRED = 6
    CANCELLED = 7


@dataclass
class AgentResult:
    """Uniform envelope for every headless operation."""

    ok: bool
    code: str = "ok"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    exit_code: int = int(ExitCode.OK)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "exit_code": int(self.exit_code),
        }


def ok(data: dict[str, Any] | None = None, *, message: str = "") -> AgentResult:
    return AgentResult(
        ok=True,
        code="ok",
        message=message,
        data=dict(data or {}),
        exit_code=int(ExitCode.OK),
    )


def fail(
    code: str,
    message: str,
    *,
    exit_code: ExitCode | int = ExitCode.ERROR,
    data: dict[str, Any] | None = None,
) -> AgentResult:
    return AgentResult(
        ok=False,
        code=code,
        message=message,
        data=dict(data or {}),
        exit_code=int(exit_code),
    )


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses / paths to JSON-friendly values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return str(value)
