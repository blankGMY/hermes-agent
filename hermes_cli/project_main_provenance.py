"""Host-owned provenance helpers for the Project MAIN pre-user seam."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

HOST_WORKSPACE_BINDING_KEY = "_hermes_project_main_workspace_binding"
_HOST_BINDING_ISSUER = "hermes-host-session-binding-v2"
_TUI_SESSION_ISSUER = object()


def _text(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, (str, Path)) and str(value).strip() else None


def _workspace(value: Any) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    absolute = Path(os.path.abspath(path))
    current = absolute
    while True:
        try:
            if current.is_symlink():
                return None
        except OSError:
            return None
        parent = current.parent
        if parent == current:
            break
        current = parent
    return os.path.normcase(os.path.normpath(str(absolute))) if absolute.is_dir() else None


@dataclass(frozen=True)
class HostWorkspaceBinding:
    session_id: str
    profile_id: str
    connection_id: str
    workspace_root: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": 2,
            "issuer": _HOST_BINDING_ISSUER,
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "connection_id": self.connection_id,
            "workspace_root": self.workspace_root,
        }


def issue_workspace_binding(
    *, session_id: Any, profile_id: Any, connection_id: Any, workspace_root: Any
) -> HostWorkspaceBinding | None:
    sid, profile, connection = _text(session_id), _text(profile_id), _text(connection_id)
    root = _workspace(workspace_root)
    if not all((sid, profile, connection, root)):
        return None
    return HostWorkspaceBinding(sid, profile, connection, root)


def persist_gateway_workspace_binding(store: Any, session_key: str, binding: HostWorkspaceBinding) -> bool:
    """Persist a binding through the normal session-store metadata path."""
    if not isinstance(binding, HostWorkspaceBinding):
        return False
    setter = getattr(store, "set_session_metadata", None)
    if not callable(setter):
        return False
    try:
        return bool(setter(session_key, HOST_WORKSPACE_BINDING_KEY, binding.to_mapping()))
    except Exception:
        return False


def _binding_from_mapping(value: Any) -> HostWorkspaceBinding | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("issuer") != _HOST_BINDING_ISSUER or value.get("version") != 2:
        return None
    session_id = _text(value.get("session_id"))
    profile_id = _text(value.get("profile_id"))
    connection_id = _text(value.get("connection_id"))
    workspace_root = _workspace(value.get("workspace_root"))
    if not all((session_id, profile_id, connection_id, workspace_root)):
        return None
    return HostWorkspaceBinding(session_id, profile_id, connection_id, workspace_root)


def _source_connection_id(source: Any) -> str | None:
    platform = getattr(getattr(source, "platform", None), "value", None) or getattr(source, "platform", None)
    chat_id = getattr(source, "chat_id", None)
    if not platform or not chat_id:
        return None
    thread = getattr(source, "thread_id", None)
    return f"{platform}:{chat_id}{':' + str(thread) if thread else ''}"


def binding_from_gateway_session(session_entry: Any, source: Any) -> HostWorkspaceBinding | None:
    metadata = getattr(session_entry, "metadata", None)
    binding = _binding_from_mapping(metadata.get(HOST_WORKSPACE_BINDING_KEY)) if isinstance(metadata, Mapping) else None
    if binding is None:
        return None
    profile_id = _text(getattr(source, "profile", None) or "default")
    connection_id = _source_connection_id(source)
    if not binding.session_id == _text(getattr(session_entry, "session_id", None)):
        return None
    if binding.profile_id != profile_id or binding.connection_id != connection_id:
        return None
    return binding


def stamp_tui_session_record(session: Any) -> Any:
    if isinstance(session, dict):
        session["_hermes_tui_session_issuer"] = _TUI_SESSION_ISSUER
    return session


def is_host_tui_session_record(session: Any) -> bool:
    return isinstance(session, dict) and session.get("_hermes_tui_session_issuer") is _TUI_SESSION_ISSUER


def tui_workspace_context(session: Any, *, session_id: str) -> dict[str, Any] | None:
    if not is_host_tui_session_record(session):
        return None
    actual_session_id = _text(session.get("session_key") or session.get("resume_session_id"))
    if not actual_session_id or actual_session_id != str(session_id):
        return None
    workspace = _workspace(session.get("cwd"))
    if workspace is None:
        return None
    return {
        "session_id": actual_session_id,
        "profile_id": _text(session.get("_hermes_profile_id")) or "default",
        "connection_id": _text(session.get("_hermes_connection_id")) or f"tui:{actual_session_id}",
        "workspace_root": workspace,
        "surface": "tui",
        "conversation_kind": "tui_chat",
        "session_title": session.get("pending_title"),
    }


def trusted_live_parent(candidate: Any) -> Any | None:
    if candidate is None:
        return None
    try:
        from gateway.run import _AGENT_PENDING_SENTINEL
        if candidate is _AGENT_PENDING_SENTINEL:
            return None
        from run_agent import AIAgent
        return candidate if isinstance(candidate, AIAgent) else None
    except Exception:
        return None
