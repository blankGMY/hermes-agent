"""Hermes lifecycle dispatch for first-party observers and plugins."""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)


def _observe(hook_name: str, **kwargs: Any) -> None:
    try:
        from hermes_cli.observability import observe_lifecycle

        observe_lifecycle(hook_name, **kwargs)
    except Exception:
        logger.warning("Built-in observability hook failed", exc_info=True)


def _plugin_hooks(hook_name: str, **kwargs: Any) -> List[Any]:
    from hermes_cli import plugins

    return plugins.invoke_hook(hook_name, **kwargs)


# Core identity token held by the trusted Hermes extension boundary for the one
# governed, behavior-changing hook. A plugin-id string supplied through kwargs
# is never authority. Python plugins execute in-process and are not a sandbox;
# hostile extension code is outside this interpreter-level trust boundary.
_CORE_PLUGIN_OWNER_PROJECT_MAIN = object()


def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    """Notify observers, then invoke compatibility plugin hooks.

    ``_core_plugin_owner`` is an internal capability, not a plugin-id input.
    Invalid owner values fail closed instead of allowing arbitrary callers to
    select a behavior-changing plugin callback.
    """
    owner = kwargs.pop("_core_plugin_owner", None)
    _observe(hook_name, **kwargs)
    if owner is not None:
        if owner is not _CORE_PLUGIN_OWNER_PROJECT_MAIN:
            return []
        return _plugin_hooks_for_owner(owner, hook_name, **kwargs)
    return _plugin_hooks(hook_name, **kwargs)


def _plugin_hooks_for_owner(owner: object, hook_name: str, **kwargs: Any) -> List[Any]:
    from hermes_cli import plugins

    if owner is not _CORE_PLUGIN_OWNER_PROJECT_MAIN:
        return []
    return plugins.invoke_hook_for_capability(owner, hook_name, **kwargs)


def has_hook(hook_name: str) -> bool:
    """Return whether a first-party observer or plugin consumes a hook."""
    try:
        from hermes_cli.observability import handles_hook

        if handles_hook(hook_name):
            return True
    except Exception:
        logger.warning("Unable to inspect built-in observability hooks", exc_info=True)

    from hermes_cli import plugins

    return plugins.has_hook(hook_name)


def finalize_session(**kwargs: Any) -> List[Any]:
    """Notify observers and hard-close one core-owned Relay conversation."""
    _observe("on_session_finalize", **kwargs)

    session_id = str(kwargs.get("session_id") or "")
    if session_id:
        try:
            from agent import relay_runtime

            relay_runtime.SESSION_COORDINATOR.finalize_conversation(
                profile_key=relay_runtime.current_profile_key(),
                session_id=session_id,
            )
        except Exception:
            logger.warning("Core Relay session finalization failed", exc_info=True)

    return _plugin_hooks("on_session_finalize", **kwargs)
