"""Core-side recognition for behavior-changing pre-user-message controls.

This module only recognizes the allowlisted control shape.  It never resolves a
project or executes a side effect; host seams use it to ensure recognized
controls cannot fall through to an ordinary model turn.

Hermes Python plugins are in-process trusted extension code, not a sandbox. The
issuer/seal helpers below defend the host boundary from serialized callers and
ordinary plugin inputs; they are not a protection boundary against deliberately
hostile Python loaded into this interpreter.
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any


CONTROL_PLANE_UNAVAILABLE_RESPONSE = (
    "PROJECT MAIN CONTROL BLOCKED\n"
    "blockers: HOST_CONTROL_PLANE_UNAVAILABLE\n"
    "next_action: verify the Hermes pre_user_message integration and framework_root"
)

# A plain dict carrying names such as ``_trusted_session_id`` is not a trust
# boundary: plugin code, a JSON-RPC client, or a test double can mutate it.  Core
# creates this mapping only after it has resolved the live session/profile route.
# The private seal makes accidental construction from a caller-owned mapping fail
# closed while keeping the value usable by legacy hook callbacks as a Mapping.
_CONTEXT_SEAL = object()
_AUTHORIZATION_SEAL = object()
_WORKSPACE_SEAL = object()
_CORE_ISSUER = object()
_CONTEXT_FIELDS = frozenset({
    "session_id", "profile_id", "connection_id", "workspace_root",
    "session_title", "conversation_kind", "surface", "profile_home",
    "bound_project_id",
})


class CoreTrustedConversationContext(Mapping[str, Any]):
    """Immutable, core-issued snapshot passed to governed pre-user hooks."""

    __slots__ = ("_values", "_seal")

    def __init__(self, values: Mapping[str, Any], *, seal: object = None) -> None:
        if seal is not _CONTEXT_SEAL or not isinstance(values, Mapping):
            raise TypeError("CORE_TRUSTED_CONTEXT_REQUIRED")
        unknown = set(values) - _CONTEXT_FIELDS
        if unknown:
            raise ValueError("CORE_TRUSTED_CONTEXT_FIELD_INVALID")
        normalized: dict[str, Any] = {}
        for key, value in values.items():
            if value is not None and not isinstance(value, (str, Path)):
                raise TypeError("CORE_TRUSTED_CONTEXT_VALUE_INVALID")
            normalized[key] = str(value) if isinstance(value, Path) else value
        object.__setattr__(self, "_values", MappingProxyType(normalized))
        object.__setattr__(self, "_seal", seal)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("CORE_TRUSTED_CONTEXT_IMMUTABLE")

    def __repr__(self) -> str:  # Do not print session/profile values into diagnostics.
        return "CoreTrustedConversationContext(<redacted>)"


class CoreTrustedAuthorization:
    """Opaque host-issued capability used for an authorized project switch."""

    __slots__ = ("_value", "_seal")

    def __init__(self, value: Any, *, seal: object = None) -> None:
        if seal is not _AUTHORIZATION_SEAL:
            raise TypeError("CORE_TRUSTED_AUTHORIZATION_REQUIRED")
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_seal", seal)

    @property
    def value(self) -> Any:
        return self._value

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("CORE_TRUSTED_AUTHORIZATION_IMMUTABLE")

    def __repr__(self) -> str:
        return "CoreTrustedAuthorization(<redacted>)"


class CoreTrustedWorkspace:
    """Immutable host snapshot used by the gateway before context stamping."""

    __slots__ = ("_value", "_seal")

    def __init__(self, value: str, *, seal: object = None) -> None:
        if seal is not _WORKSPACE_SEAL or not isinstance(value, str) or not value:
            raise TypeError("CORE_TRUSTED_WORKSPACE_REQUIRED")
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_seal", seal)

    @property
    def value(self) -> str:
        return self._value

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("CORE_TRUSTED_WORKSPACE_IMMUTABLE")

    def __repr__(self) -> str:
        return "CoreTrustedWorkspace(<redacted>)"


def _stamp_core_trusted_authorization(value: Any, *, issuer: object = None) -> CoreTrustedAuthorization:
    """Wrap a capability only for the private Hermes core issuer."""
    if issuer is not _CORE_ISSUER:
        raise TypeError("CORE_ISSUER_REQUIRED")
    return CoreTrustedAuthorization(value, seal=_AUTHORIZATION_SEAL)


def unwrap_core_trusted_authorization(value: object) -> Any:
    """Return a capability payload only when its opaque host stamp is valid."""
    if (
        type(value) is CoreTrustedAuthorization
        and getattr(value, "_seal", None) is _AUTHORIZATION_SEAL
    ):
        return value.value
    return None


def _stamp_core_trusted_context(
    values: Mapping[str, Any], *, issuer: object = None
) -> CoreTrustedConversationContext:
    """Issue a context only for the private Hermes core issuer."""
    if issuer is not _CORE_ISSUER:
        raise TypeError("CORE_ISSUER_REQUIRED")
    normalized = dict(values)
    if normalized.get("workspace_root") is not None:
        workspace_root = snapshot_core_workspace(normalized["workspace_root"])
        if workspace_root is None:
            raise ValueError("CORE_TRUSTED_CONTEXT_WORKSPACE_INVALID")
        normalized["workspace_root"] = workspace_root
    return CoreTrustedConversationContext(normalized, seal=_CONTEXT_SEAL)


def is_core_stamped_context(value: object) -> bool:
    """Return whether *value* is an unmodified context issued by this module."""
    return (
        type(value) is CoreTrustedConversationContext
        and getattr(value, "_seal", None) is _CONTEXT_SEAL
    )


def _replace_core_trusted_context(
    value: CoreTrustedConversationContext, *, issuer: object = None, **updates: Any
) -> CoreTrustedConversationContext:
    """Replace a context only for the private Hermes core issuer."""
    if issuer is not _CORE_ISSUER:
        raise TypeError("CORE_ISSUER_REQUIRED")
    if not is_core_stamped_context(value):
        raise TypeError("CORE_TRUSTED_CONTEXT_REQUIRED")
    current = dict(value)
    current.update(updates)
    return _stamp_core_trusted_context(current, issuer=_CORE_ISSUER)


def _safe_recorded_workspace(value: object) -> str | None:
    """Return an existing absolute non-reparse workspace, or ``None``."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        return None
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return None
        absolute = Path(os.path.abspath(candidate))
        for component in reversed((absolute, *absolute.parents)):
            try:
                info = os.lstat(component)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or int(getattr(info, "st_file_attributes", 0)) & 0x0400:
                return None
        return str(absolute) if os.path.isdir(absolute) else None
    except (OSError, TypeError, ValueError):
        return None


def snapshot_core_workspace(value: str | Path | None) -> str | None:
    """Capture an explicit host workspace without consulting ambient process state."""
    return _safe_recorded_workspace(value)


def is_core_workspace_current(value: str | Path | None) -> bool:
    """Revalidate a stamped workspace immediately before privileged use.

    Stamping a path is not a durable filesystem handle.  Re-run the complete
    ancestor and directory checks at the use site so a replacement junction,
    symlink, or missing directory fails closed instead of being followed.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    return _safe_recorded_workspace(value) == value


def _stamp_core_workspace(
    value: str | Path | None, *, issuer: object = None
) -> CoreTrustedWorkspace | None:
    """Issue an immutable workspace snapshot only for the private core issuer."""
    if issuer is not _CORE_ISSUER:
        raise TypeError("CORE_ISSUER_REQUIRED")
    normalized = snapshot_core_workspace(value)
    return CoreTrustedWorkspace(normalized, seal=_WORKSPACE_SEAL) if normalized else None


def unwrap_core_workspace(value: object) -> str | None:
    """Return a workspace only from an unmodified core-issued snapshot."""
    if type(value) is CoreTrustedWorkspace and getattr(value, "_seal", None) is _WORKSPACE_SEAL:
        return value.value
    return None


def core_context_from_cli_session(
    *,
    session_meta: Mapping[str, Any],
    session_id: str,
    profile_id: str,
    profile_home: str | Path | None,
    connection_id: str = "cli",
) -> CoreTrustedConversationContext:
    """Derive a CLI resume context only from the durable session row.

    A resumed process may start in any directory.  The current process CWD is
    therefore never consulted here; the stored session id, owning profile, CLI
    source, and recorded workspace must all authenticate the snapshot.
    """
    if not isinstance(session_meta, Mapping):
        raise ValueError("CURRENT_RESUME_CONTEXT_UNAVAILABLE")
    if (
        not isinstance(session_id, str)
        or not isinstance(profile_id, str)
        or not isinstance(connection_id, str)
        or not session_id.strip()
        or not profile_id.strip()
        or not connection_id.strip()
        or session_meta.get("id") != session_id
        or str(session_meta.get("source") or "").casefold() != "cli"
        or session_meta.get("profile_name") != profile_id
    ):
        raise ValueError("CURRENT_RESUME_CONTEXT_UNAVAILABLE")
    workspace_root = _safe_recorded_workspace(session_meta.get("cwd"))
    if workspace_root is None:
        raise ValueError("CURRENT_RESUME_CONTEXT_UNAVAILABLE")
    if profile_home is not None:
        if not isinstance(profile_home, (str, Path)):
            raise ValueError("CURRENT_RESUME_CONTEXT_UNAVAILABLE")
        profile_home = str(profile_home)
        try:
            if not Path(profile_home).expanduser().is_absolute():
                raise ValueError("CURRENT_RESUME_CONTEXT_UNAVAILABLE")
        except (OSError, TypeError, ValueError) as exc:
            if str(exc) == "CURRENT_RESUME_CONTEXT_UNAVAILABLE":
                raise
            raise ValueError("CURRENT_RESUME_CONTEXT_UNAVAILABLE") from exc
    title = session_meta.get("title")
    return _stamp_core_trusted_context({
        "session_id": session_id,
        "profile_id": profile_id,
        "connection_id": connection_id,
        "workspace_root": workspace_root,
        "session_title": title if isinstance(title, str) else "",
        "conversation_kind": "cli_chat",
        "surface": "cli",
        "profile_home": profile_home,
        "bound_project_id": None,
    }, issuer=_CORE_ISSUER)


_MARKER_RE = re.compile(
    r"^\s*@memory\s+SET_CURRENT_CHAT_AS_MAIN"
    r"(?:\s+(?:project|project_id)\s*=\s*"
    r"[A-Za-z0-9][A-Za-z0-9_-]{0,63})?\s*$",
    re.IGNORECASE,
)
_CHINESE_RE = re.compile(
    r"(?:请\s*)?(?:将|把)?\s*(?:当前|这个)?\s*(?:对话|聊天|会话)\s*"
    r"(?:设为|设置为|绑定为)\s*"
    r"(?:(?:项目)\s*(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63})?\s*)?"
    r"(?:MAIN|主会话|项目主会话)\s*[。.!！]*$",
    re.IGNORECASE,
)
_INITIALIZE_RE = re.compile(
    r"(?:请\s*)?初始化\s*(?:当前\s*)?项目\s*"
    r"(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}\s*)?MAIN\s*[。.!！]*$",
    re.IGNORECASE,
)
_ENGLISH_RE = re.compile(
    r"(?:please\s+)?set\s+(?:the\s+)?(?:current|this)\s+"
    r"(?:chat|conversation|session)\s+as\s+"
    r"(?:project\s+(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}\s*)?)?"
    r"main\s*[.!]*$",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).strip())


def is_project_main_control_message(text: object) -> bool:
    """Return whether *text* is a complete, recognized control trigger."""
    if not isinstance(text, str):
        return False
    normalized = _normalize(text)
    if not normalized:
        return False
    return bool(
        _MARKER_RE.fullmatch(normalized)
        or _CHINESE_RE.fullmatch(normalized)
        or _INITIALIZE_RE.fullmatch(normalized)
        or _ENGLISH_RE.fullmatch(normalized)
    )


__all__ = [
    "CONTROL_PLANE_UNAVAILABLE_RESPONSE",
    "CoreTrustedAuthorization",
    "CoreTrustedConversationContext",
    "CoreTrustedWorkspace",
    "core_context_from_cli_session",
    "is_core_stamped_context",
    "is_core_workspace_current",
    "is_project_main_control_message",
    "snapshot_core_workspace",
    "unwrap_core_trusted_authorization",
    "unwrap_core_workspace",
]
