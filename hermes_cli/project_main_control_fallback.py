"""Minimal exact Project MAIN trigger fallback for classifier-load failures.

This module has no host or plugin dependencies. It is used only to preserve the
control-plane terminality invariant when the primary classifier cannot import:
recognized controls are still consumed and blocked, while ordinary text passes
through to the normal model path.
"""

from __future__ import annotations

import re
import unicodedata

CONTROL_PLANE_UNAVAILABLE_RESPONSE = (
    "PROJECT MAIN CONTROL BLOCKED\n"
    "blockers: HOST_CONTROL_PLANE_UNAVAILABLE\n"
    "next_action: verify the Hermes pre_user_message integration and framework_root"
)

_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*@memory\s+SET_CURRENT_CHAT_AS_MAIN"
        r"(?:\s+(?:project|project_id)\s*=\s*[A-Za-z0-9][A-Za-z0-9_-]{0,63})?\s*$",
        r"(?:请\s*)?(?:将|把)?\s*(?:当前|这个)?\s*(?:对话|聊天|会话)\s*"
        r"(?:设为|设置为|绑定为)\s*"
        r"(?:(?:项目)\s*(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63})?\s*)?"
        r"(?:MAIN|主会话|项目主会话)\s*[。.!！]*$",
        r"(?:请\s*)?初始化\s*(?:当前\s*)?项目\s*"
        r"(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}\s*)?MAIN\s*[。.!！]*$",
        r"(?:please\s+)?set\s+(?:the\s+)?(?:current|this)\s+"
        r"(?:chat|conversation|session)\s+as\s+"
        r"(?:project\s+(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}\s*)?)?"
        r"main\s*[.!]*$",
    )
)


def is_project_main_control_message_fallback(message: object) -> bool:
    """Recognize only complete allowlisted control forms; never use substrings."""
    if not isinstance(message, str):
        return False
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", message).strip())
    return bool(normalized) and any(pattern.fullmatch(normalized) for pattern in _PATTERNS)


__all__ = [
    "CONTROL_PLANE_UNAVAILABLE_RESPONSE",
    "is_project_main_control_message_fallback",
]
