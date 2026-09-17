"""Core-side behavior-changing pre-user-message seam.

The host owns recognition, session provenance, and the in-process trust seal.
Plugins may operate only on a sealed context. Missing or invalid provenance is
fail-closed for recognized Project MAIN controls and pass-through for ordinary
messages.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from hermes_cli.project_main_provenance import (
    binding_from_gateway_session,
    trusted_live_parent,
    tui_workspace_context,
)


_CORE_ISSUER = object()
CONTROL_PLANE_UNAVAILABLE_RESPONSE = (
    "PROJECT MAIN CONTROL BLOCKED\n"
    "blockers: HOST_CONTROL_PLANE_UNAVAILABLE"
)
_CONTROL_RE = re.compile(
    r"^\s*(?:@memory\s+SET_CURRENT_CHAT_AS_MAIN|"
    r"(?:请\s*)?(?:将|把)?\s*(?:当前|这个)?\s*(?:对话|聊天|会话)\s*"
    r"(?:设为|设置为|绑定为)\s*(?:(?:项目)(?:\s*[A-Za-z0-9][A-Za-z0-9_-]{0,63})?\s*)?"
    r"(?:MAIN|主会话|项目主会话))\s*[。.!！]*\s*$",
    re.IGNORECASE,
)


def is_project_main_control_message(message: object) -> bool:
    return isinstance(message, str) and _CONTROL_RE.fullmatch(message) is not None


def _stamp_core_trusted_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError("HOST_CONTEXT_INVALID")
    result = dict(context)
    result["__hermes_core_issuer"] = _CORE_ISSUER
    return result


def is_core_stamped_context(context: object) -> bool:
    return isinstance(context, Mapping) and context.get("__hermes_core_issuer") is _CORE_ISSUER


def core_context_from_cli_session(cli: Any) -> dict[str, Any] | None:
    candidate = getattr(cli, "_project_main_host_context", None)
    if not isinstance(candidate, Mapping):
        return None
    workspace = candidate.get("workspace_root")
    if not isinstance(workspace, str) or not workspace:
        return None
    return _stamp_core_trusted_context(candidate)


def core_context_from_gateway(*, session_entry: Any, source: Any, workspace_root: str | None = None) -> dict[str, Any] | None:
    del workspace_root  # process-level cwd is never a Project MAIN authority
    binding = binding_from_gateway_session(session_entry, source)
    if binding is None:
        return None
    return _stamp_core_trusted_context({
        "session_id": binding.session_id,
        "profile_id": binding.profile_id,
        "connection_id": binding.connection_id,
        "workspace_root": binding.workspace_root,
        "surface": "gateway",
        "conversation_kind": "gateway_chat",
        "session_title": getattr(session_entry, "display_name", None),
    })


def core_context_from_tui_session(session: Mapping[str, Any], *, session_id: str) -> dict[str, Any] | None:
    context = tui_workspace_context(session, session_id=session_id)
    return _stamp_core_trusted_context(context) if context is not None else None


def dispatch_pre_user_message(
    message: str,
    *,
    context: Mapping[str, Any] | None,
    surface: str,
    parent_agent: Any | None = None,
    session_busy: bool = False,
) -> tuple[bool, str | None]:
    """Invoke behavior-changing hooks before persistence/model execution."""
    _recognized = is_project_main_control_message(message)
    recognized = _recognized
    if context is None or not is_core_stamped_context(context):
        return (True, CONTROL_PLANE_UNAVAILABLE_RESPONSE) if recognized else (False, None)
    try:
        from hermes_cli.lifecycle import invoke_hook
        results = invoke_hook(
            "pre_user_message",
            message=message,
            context=context,
            surface=surface,
            parent_agent=parent_agent,
            session_busy=session_busy,
        )
    except Exception:
        return (True, CONTROL_PLANE_UNAVAILABLE_RESPONSE) if recognized else (False, None)
    for result in results if isinstance(results, (list, tuple)) else ():
        if isinstance(result, Mapping) and result.get("action") == "handled":
            return True, str(result.get("response") or "")
    return (True, CONTROL_PLANE_UNAVAILABLE_RESPONSE) if recognized else (False, None)
