"""Regression tests for the governed Project MAIN host boundary."""

import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import lifecycle


def _stamp_context(values):
    from hermes_cli.pre_user_message import _CORE_ISSUER, _stamp_core_trusted_context
    return _stamp_core_trusted_context(values, issuer=_CORE_ISSUER)


def _stamp_workspace(value):
    from hermes_cli.pre_user_message import _CORE_ISSUER, _stamp_core_workspace
    return _stamp_core_workspace(value, issuer=_CORE_ISSUER)


def test_core_context_stamps_require_the_private_issuer():
    """Caller-controlled values cannot mint a context accepted by the host."""
    import hermes_cli.pre_user_message as trust

    assert not hasattr(trust, "stamp_core_trusted_context")
    with pytest.raises(TypeError, match="CORE_ISSUER_REQUIRED"):
        trust._stamp_core_trusted_context({"session_id": "attacker"})


def test_string_cannot_select_a_privileged_plugin_owner():
    """A caller-controlled owner name must not invoke the governed callback."""
    with patch.object(lifecycle, "_plugin_hooks_for_owner") as dispatch:
        result = lifecycle.invoke_hook(
            "pre_user_message",
            _core_plugin_owner="project-main-binding",
            message="将当前对话设为项目 MAIN",
        )

    assert result == []
    dispatch.assert_not_called()


def test_cli_classifier_failure_does_not_consume_ordinary_prompt(monkeypatch):
    """A missing control classifier must not block unrelated user text."""
    import builtins

    from cli import _pre_user_message_control

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "hermes_cli.pre_user_message":
            raise ImportError("classifier unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    assert _pre_user_message_control(SimpleNamespace(), "ordinary prompt") == (False, None)
    handled, response = _pre_user_message_control(
        SimpleNamespace(), "将当前对话设为项目 MAIN"
    )
    assert handled is True
    assert "HOST_CONTROL_PLANE_UNAVAILABLE" in response


def test_cli_rejects_a_core_context_from_another_surface(tmp_path):
    """CLI control dispatch accepts only its own stamped surface identity."""
    from hermes_cli.pre_user_message import _CORE_ISSUER, _stamp_core_trusted_context
    from cli import _pre_user_message_control

    context = _stamp_core_trusted_context(
        {
            "session_id": "session-1",
            "profile_id": "default",
            "connection_id": "tui-gateway",
            "workspace_root": str(tmp_path),
            "surface": "tui",
            "conversation_kind": "tui_chat",
        },
        issuer=_CORE_ISSUER,
    )
    fake_cli = SimpleNamespace(
        _trusted_current_conversation_context=context,
        _agent_running=False,
    )
    with patch("hermes_cli.lifecycle.invoke_hook") as invoke:
        handled, response = _pre_user_message_control(
            fake_cli, "将当前对话设为项目 MAIN"
        )
    assert handled is True
    assert "HOST_CONTEXT_OR_CONTROL_PLANE_UNAVAILABLE" in response
    invoke.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_classifier_failure_does_not_consume_ordinary_prompt(monkeypatch):
    """Gateway ordinary text still reaches the normal path if the classifier is unavailable."""
    import builtins

    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "hermes_cli.pre_user_message":
            raise ImportError("classifier unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    runner = object.__new__(GatewayRunner)
    source = SessionSource(platform=Platform.LOCAL, chat_id="chat")
    event = MessageEvent(text="ordinary prompt", source=source)
    assert await runner._hm_pre_user_message_control(event, source, "route-key") is None


def test_tui_classifier_failure_does_not_consume_ordinary_prompt(monkeypatch):
    """TUI ordinary text still reaches the normal path if the classifier is unavailable."""
    import builtins

    from tui_gateway import server

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "hermes_cli.pre_user_message":
            raise ImportError("classifier unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    assert server._pre_user_message_control(
        "req-1", "session-1", {}, "ordinary prompt", {}
    ) is None


def test_cli_revalidates_workspace_before_privileged_hook(tmp_path, monkeypatch):
    """CLI must re-check the stamped workspace immediately before dispatch."""
    from hermes_cli import pre_user_message
    from cli import _pre_user_message_control

    context = _stamp_context(
        {
            "session_id": "session-1",
            "profile_id": "default",
            "connection_id": "cli",
            "workspace_root": str(tmp_path),
            "surface": "cli",
            "conversation_kind": "cli_chat",
        }
    )
    fake_cli = SimpleNamespace(
        _trusted_current_conversation_context=context,
        _agent_running=False,
    )
    monkeypatch.setattr(pre_user_message, "is_core_workspace_current", lambda _value: False)
    with patch("hermes_cli.lifecycle.invoke_hook") as invoke:
        handled, response = _pre_user_message_control(
            fake_cli, "将当前对话设为项目 MAIN"
        )
    assert handled is True
    assert "HOST_CONTEXT_OR_CONTROL_PLANE_UNAVAILABLE" in response
    invoke.assert_not_called()


def test_tui_explicit_workspace_move_rejects_an_unsafe_recorded_path(tmp_path, monkeypatch):
    """A TUI cwd change must not stamp a reparse/symlink workspace as trusted."""
    from hermes_cli import pre_user_message
    from tui_gateway import server

    monkeypatch.setattr(pre_user_message, "snapshot_core_workspace", lambda _value: None)
    session = {
        "session_key": "session-1",
        "cwd": str(tmp_path),
        "explicit_cwd": False,
        "cwd_from_settle": True,
    }
    with pytest.raises(ValueError, match="safe workspace"):
        server._set_session_cwd(session, str(tmp_path))
    assert session["explicit_cwd"] is False


def test_tui_context_uses_the_core_workspace_validator(tmp_path, monkeypatch):
    """TUI context issuance must fail closed when workspace validation rejects a path."""
    from hermes_cli import pre_user_message
    from tui_gateway import methods_session

    monkeypatch.setattr(pre_user_message, "snapshot_core_workspace", lambda _value: None)
    with pytest.raises(ValueError, match="CORE_TRUSTED_CONTEXT_WORKSPACE_INVALID"):
        methods_session._core_tui_context(
            "session-1",
            {"cwd": str(tmp_path), "explicit_cwd": True, "profile_home": None},
        )


def test_gateway_legacy_workspace_attribute_is_not_authority(tmp_path):
    """The gateway must not accept a mutable path attribute as trusted context."""
    from gateway.run import GatewayRunner

    source = SimpleNamespace(_trusted_workspace_root=str(Path(tmp_path)))
    assert GatewayRunner._hm_control_workspace_root(source, None) is None


def test_core_owner_capability_selects_only_the_recorded_owner():
    """The host capability remains an exact identity token, not a plugin id string."""
    owner = lifecycle._CORE_PLUGIN_OWNER_PROJECT_MAIN
    with patch.object(lifecycle, "_plugin_hooks_for_owner", return_value=["handled"]) as dispatch:
        result = lifecycle.invoke_hook(
            "pre_user_message",
            _core_plugin_owner=owner,
            message="将当前对话设为项目 MAIN",
        )

    assert result == ["handled"]
    dispatch.assert_called_once()
    assert dispatch.call_args.args[0] is owner
    assert dispatch.call_args.args[1] == "pre_user_message"


def test_reserved_project_main_owner_requires_the_profile_local_plugin(tmp_path):
    """A same-named plugin outside the consented profile root cannot receive the core hook."""
    from hermes_cli.plugins import PluginManager, PluginManifest, _is_trusted_project_main_manifest

    manager = PluginManager(scope_key=str(tmp_path))
    manager.home_path = Path(tmp_path)
    expected = manager.home_path / "plugins" / "project-main-binding"
    trusted = PluginManifest(
        name="project-main-binding", key="project-main-binding", source="user", path=str(expected)
    )
    project_copy = PluginManifest(
        name="project-main-binding", key="project-main-binding", source="project",
        path=str(Path(tmp_path) / "project" / "project-main-binding"),
    )
    assert _is_trusted_project_main_manifest(trusted, manager.home_path)
    assert not _is_trusted_project_main_manifest(project_copy, manager.home_path)

    manager._hooks = {"pre_user_message": [lambda **_kwargs: "should-not-run"]}
    manager._hook_owners = {"pre_user_message": ["project-main-binding"]}
    manager._privileged_hook_owners = set()
    assert manager.invoke_hook_for_plugin("project-main-binding", "pre_user_message") == []


def test_reserved_project_main_owner_rejects_reparse_plugin_parent(tmp_path, monkeypatch):
    """A junction/reparse at ``home/plugins`` cannot grant profile-local trust."""
    from hermes_cli import plugins
    from hermes_cli.plugins import PluginManifest, _is_trusted_project_main_manifest

    home = Path(tmp_path)
    expected = home / "plugins" / "project-main-binding"
    manifest = PluginManifest(
        name="project-main-binding", key="project-main-binding", source="user",
        path=str(expected),
    )
    real_lstat = os.lstat
    reparse_parent = home / "plugins"

    def lstat_with_reparse_parent(path):
        if Path(path) == reparse_parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x0400)
        return real_lstat(path)

    monkeypatch.setattr(plugins.os, "lstat", lstat_with_reparse_parent)
    assert not _is_trusted_project_main_manifest(manifest, home)


def test_reserved_project_main_owner_dispatches_only_after_trusted_registration(tmp_path):
    """The exact recorded owner is usable only after the manager accepts its trusted manifest."""
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    manager = PluginManager(scope_key=str(tmp_path))
    owner = lifecycle._CORE_PLUGIN_OWNER_PROJECT_MAIN
    manifest = PluginManifest(
        name="project-main-binding", key="project-main-binding", source="user",
        path=str(Path(tmp_path) / "plugins" / "project-main-binding"),
    )
    PluginContext(manifest, manager, owner_capability=owner).register_hook(
        "pre_user_message", lambda **_kwargs: "handled"
    )
    assert manager._hook_owners["pre_user_message"][0] is owner
    assert manager.invoke_hook_for_plugin("project-main-binding", "pre_user_message") == []
    assert manager.invoke_hook_for_capability(owner, "pre_user_message") == ["handled"]


def test_pre_gateway_discovery_does_not_block_the_event_loop(monkeypatch):
    """Lazy plugin discovery must fail closed without a synchronous join."""
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource
    from hermes_cli import plugins

    class SlowManager:
        _discovered = False

        def discover_and_load(self):
            time.sleep(0.25)
            self._discovered = True

        def invoke_hook(self, *_args, **_kwargs):
            return []

    manager = SlowManager()
    started = False

    def start_discovery():
        nonlocal started
        started = True

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(plugins, "start_background_plugin_discovery", start_discovery)
    monkeypatch.setattr(lifecycle, "_observe", lambda *_args, **_kwargs: None)

    runner = object.__new__(GatewayRunner)
    source = SessionSource(platform=Platform.LOCAL, chat_id="chat")
    event = MessageEvent(text="ordinary prompt", source=source)

    started_at = time.monotonic()
    result = runner._hm_pre_gateway_dispatch_hook(event, source)
    elapsed = time.monotonic() - started_at

    assert result is None
    assert started is True
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_unauthorized_gateway_event_never_reaches_behavior_hook():
    """An unauthenticated sender cannot use a plugin to suppress or rewrite ingress."""
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    runner = object.__new__(GatewayRunner)
    runner._scale_to_zero_note_real_inbound = lambda: None
    runner._is_user_authorized_for_source = lambda _source, **_kwargs: False
    runner._hm_pre_gateway_dispatch_hook = lambda *_args: pytest.fail(
        "unauthorized events must not reach pre_gateway_dispatch"
    )
    source = SessionSource(platform=Platform.LOCAL, chat_id="chat")
    assert await runner._hm_admit_event(MessageEvent(text="ordinary", source=source)) is None


@pytest.mark.asyncio
async def test_gateway_control_requires_an_existing_session_route(tmp_path):
    """A stamped context alone cannot mint a gateway conversation binding."""
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource
    from gateway.turn_lease import SessionTurnLeaseRegistry

    runner = object.__new__(GatewayRunner)
    runner._turn_leases = SessionTurnLeaseRegistry()
    runner._is_session_running = lambda _key: False
    runner.session_store = SimpleNamespace(peek_session_id=lambda _key: None)
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="local-chat",
        user_id="user-1",
        profile="default",
    )
    source._core_trusted_context = _stamp_context({
        "session_id": "context-only-session",
        "profile_id": "default",
        "connection_id": "gateway",
        "workspace_root": str(tmp_path),
        "surface": "gateway",
        "conversation_kind": "gateway_chat",
    })

    with patch("hermes_cli.lifecycle.invoke_hook") as invoke:
        response = await runner._hm_pre_user_message_control(
            MessageEvent(text="将当前对话设为项目 MAIN", source=source),
            source,
            "route-key",
        )

    assert "HOST_CONTEXT_OR_CONTROL_PLANE_UNAVAILABLE" in response
    invoke.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_control_is_terminal_after_auth_without_turn_creation(tmp_path):
    """A stamped gateway control is handled before the ordinary turn pipeline."""
    import weakref
    from datetime import datetime

    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionEntry, SessionSource
    from gateway.turn_lease import SessionTurnLeaseRegistry

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner._primary_profile_name = "default"
    runner._core_gateway_workspace_root = _stamp_workspace(tmp_path)
    runner._scale_to_zero_note_real_inbound = lambda: None
    runner._session_key_for_source = lambda _source: "route-key"
    runner._is_user_authorized_for_source = lambda _source, **_kwargs: True
    runner._admit_bot_message_for_source = lambda _source: True
    runner._is_session_running = lambda _key: False
    runner._turn_leases = SessionTurnLeaseRegistry()
    class Adapter:
        pass

    adapter = Adapter()
    runner.adapters = {Platform.LOCAL: adapter}
    runner._profile_adapters = {}
    route = SessionEntry(
        session_key="route-key",
        session_id="durable-session",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=SessionSource(
            platform=Platform.LOCAL,
            chat_id="local-chat",
            chat_type="dm",
            user_id="user-1",
            profile="default",
        ),
        platform=Platform.LOCAL,
        chat_type="dm",
    )
    runner.session_store = SimpleNamespace(
        peek_session_id=lambda key: "durable-session" if key == "route-key" else None,
        lookup_by_session_key=lambda key: route if key == "route-key" else None,
        get_or_create_session=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("control must not create a session")
        ),
    )
    runner._hm_pre_gateway_dispatch_hook = lambda *_args: pytest.fail(
        "literal controls must not enter the pre-gateway rewrite hook"
    )
    runner._run_agent = pytest.fail

    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="local-chat",
        chat_type="dm",
        user_id="user-1",
        profile="default",
    )
    source._transport_adapter_ref = weakref.ref(adapter)
    event = MessageEvent(text="将当前对话设为项目 MAIN", source=source)

    with patch(
        "hermes_cli.lifecycle.invoke_hook",
        return_value=[{
            "action": "handled",
            "handler": "project-main-binding",
            "response": "PROJECT MAIN READY",
        }],
    ) as invoke:
        response = await runner._handle_message(event)

    assert response == "PROJECT MAIN READY"
    invoke.assert_called_once()
    assert invoke.call_args.kwargs["message"] == "将当前对话设为项目 MAIN"
    assert invoke.call_args.kwargs["session_id"] == "durable-session"


def test_gateway_core_stamps_context_from_durable_route_and_explicit_host_workspace(tmp_path):
    """Gateway identity must be stamped by core after route resolution, not copied from source fields."""
    import weakref
    from datetime import datetime

    from gateway.config import Platform
    from gateway.run import GatewayRunner
    from gateway.session import SessionEntry, SessionSource
    from hermes_cli.pre_user_message import is_core_stamped_context

    class Adapter:
        pass

    adapter = Adapter()
    adapter._owner_profile = "attacker-controlled"
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner.adapters = {Platform.LOCAL: adapter}
    runner._profile_adapters = {}
    runner._primary_profile_name = "default"
    runner._core_gateway_workspace_root = _stamp_workspace(tmp_path)

    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="local-chat",
        chat_type="dm",
        user_id="user-1",
    )
    source._transport_adapter_ref = weakref.ref(adapter)
    origin = SessionSource(
        platform=Platform.LOCAL,
        chat_id="local-chat",
        chat_type="dm",
        user_id="user-1",
        profile="default",
    )
    route = SessionEntry(
        session_key="route-key",
        session_id="durable-session",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=origin,
        platform=Platform.LOCAL,
        chat_type="dm",
    )

    context = runner._hm_core_gateway_context(
        source,
        quick_key="route-key",
        session_id="durable-session",
        route_entry=route,
    )

    assert is_core_stamped_context(context)
    assert context["session_id"] == "durable-session"
    assert context["profile_id"] == "default"
    assert context["workspace_root"] == str(tmp_path)
    assert context["surface"] == "gateway"
    assert context["connection_id"] == "gateway:default:local"


def test_gateway_core_context_does_not_fall_back_to_terminal_cwd(tmp_path, monkeypatch):
    """An ambient TERMINAL_CWD is not a gateway conversation identity source."""
    import weakref
    from datetime import datetime

    from gateway.config import Platform
    from gateway.run import GatewayRunner
    from gateway.session import SessionEntry, SessionSource

    class Adapter:
        pass

    adapter = Adapter()
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner.adapters = {Platform.LOCAL: adapter}
    runner._profile_adapters = {}
    runner._primary_profile_name = "default"
    runner._core_gateway_workspace_root = None
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    source = SessionSource(platform=Platform.LOCAL, chat_id="local-chat", user_id="user-1")
    source._transport_adapter_ref = weakref.ref(adapter)
    route = SessionEntry(
        session_key="route-key",
        session_id="durable-session",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=SessionSource(
            platform=Platform.LOCAL, chat_id="local-chat", user_id="user-1", profile="default"
        ),
        platform=Platform.LOCAL,
        chat_type="dm",
    )

    assert runner._hm_core_gateway_context(
        source, quick_key="route-key", session_id="durable-session", route_entry=route
    ) is None
    runner._core_gateway_workspace_root = str(tmp_path)
    assert runner._hm_core_gateway_context(
        source, quick_key="route-key", session_id="durable-session", route_entry=route
    ) is None


@pytest.mark.asyncio
async def test_real_authenticated_gateway_control_resolves_one_durable_route_before_hook(
    tmp_path, monkeypatch
):
    """The production inbound path must stamp the final durable route without creating a turn."""
    import weakref

    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionStore
    from gateway.turn_lease import SessionTurnLeaseRegistry
    from hermes_cli import lifecycle
    from hermes_cli.pre_user_message import is_core_stamped_context

    class AuthenticatedWebhookAdapter(BasePlatformAdapter):
        async def connect(self, *, is_reconnect: bool = False) -> bool:
            return True

        async def disconnect(self) -> None:
            return None

        async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
            return SendResult(success=True)

        async def get_chat_info(self, chat_id):
            return {"name": chat_id, "type": "dm"}

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    config = GatewayConfig(
        platforms={Platform.WEBHOOK: PlatformConfig(enabled=True)},
        sessions_dir=tmp_path / "sessions",
        current_chat_main_workspace_root=str(tmp_path),
    )
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner._primary_profile_name = "default"
    runner._core_gateway_workspace_root = _stamp_workspace(tmp_path)
    runner._scale_to_zero_note_real_inbound = lambda: None
    runner._is_session_running = lambda _key: False
    runner._turn_leases = SessionTurnLeaseRegistry()
    runner._profile_name_for_source = lambda _source, adapter_profile=None: "default"
    runner._profile_adapters = {}
    adapter = AuthenticatedWebhookAdapter(config.platforms[Platform.WEBHOOK], Platform.WEBHOOK)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.WEBHOOK: adapter}
    store = SessionStore(config.sessions_dir, config)
    store._db = None
    runner.session_store = store

    source = adapter.build_source(
        chat_id="authenticated-chat",
        chat_type="dm",
        user_id="authenticated-user",
    )
    route = store.get_or_create_session(source)
    quick_key = runner._session_key_for_source(source)
    assert route.session_key == quick_key

    class CountingStore:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.lookup_count = 0
            self.peek_count = 0

        def lookup_by_session_key(self, key):
            self.lookup_count += 1
            return self.wrapped.lookup_by_session_key(key)

        def peek_session_id(self, key):
            self.peek_count += 1
            return self.wrapped.lookup_by_session_key(key).session_id

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    counting_store = CountingStore(store)
    runner.session_store = counting_store
    hook_contexts = []

    def registered_project_main_hook(_owner, hook_name, **kwargs):
        assert hook_name == "pre_user_message"
        hook_contexts.append(kwargs["context"])
        return [{
            "action": "handled",
            "handler": "project-main-binding",
            "response": "PROJECT MAIN READY",
        }]

    monkeypatch.setattr(lifecycle, "_plugin_hooks_for_owner", registered_project_main_hook)
    runner._run_agent = lambda *_args, **_kwargs: pytest.fail("control fell through to model dispatch")
    runner._hm_pre_gateway_dispatch_hook = lambda *_args: pytest.fail(
        "literal control entered the ordinary pre-gateway hook"
    )

    response = await runner._handle_message(
        MessageEvent(text="将当前对话设为项目 MAIN", source=source)
    )

    assert response == "PROJECT MAIN READY"
    assert counting_store.lookup_count == 1
    # The post-lease peek is only a consistency check; route resolution itself is one lookup.
    assert counting_store.peek_count == 1
    assert len(hook_contexts) == 1
    context = hook_contexts[0]
    assert is_core_stamped_context(context)
    assert context["session_id"] == route.session_id
    assert context["profile_id"] == "default"
    assert source._transport_adapter_ref() is adapter


@pytest.mark.asyncio
async def test_gateway_control_uses_final_durable_route_after_topic_recovery(tmp_path):
    """Control binding follows a read-only recovered route, not the raw lobby key."""
    import weakref
    from datetime import datetime

    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionEntry, SessionSource
    from gateway.turn_lease import SessionTurnLeaseRegistry
    from hermes_cli.pre_user_message import is_core_stamped_context

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner._primary_profile_name = "default"
    runner._core_gateway_workspace_root = _stamp_workspace(tmp_path)
    runner._scale_to_zero_note_real_inbound = lambda: None
    runner._is_user_authorized_for_source = lambda _source, **_kwargs: True
    runner._admit_bot_message_for_source = lambda _source: True
    runner._is_session_running = lambda _key: False
    runner._turn_leases = SessionTurnLeaseRegistry()
    runner._profile_adapters = {}

    class Adapter:
        pass

    adapter = Adapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    route = SessionEntry(
        session_key="recovered-key",
        session_id="durable-session",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="telegram-chat",
            chat_type="dm",
            user_id="user-1",
            thread_id="42",
            profile="default",
        ),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = SimpleNamespace(
        lookup_by_session_key=lambda key: route if key == "recovered-key" else None,
        peek_session_id=lambda key: route.session_id if key == "recovered-key" else None,
        get_or_create_session=lambda *_args, **_kwargs: pytest.fail(
            "control must not create a session"
        ),
        _generate_session_key=lambda source: (
            "recovered-key" if source.thread_id == "42" else "raw-lobby-key"
        ),
    )
    runner._session_key_for_source = lambda source: runner.session_store._generate_session_key(source)
    runner._is_telegram_dm = lambda _source: True
    runner._telegram_topic_mode_enabled = lambda _source: True
    runner._recover_telegram_topic_thread_id = lambda _source: "42"
    runner._hm_pre_gateway_dispatch_hook = lambda *_args: pytest.fail(
        "literal control entered the pre-gateway rewrite hook"
    )
    runner._run_agent = lambda *_args, **_kwargs: pytest.fail(
        "control fell through to model dispatch"
    )

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="telegram-chat",
        chat_type="dm",
        user_id="user-1",
        thread_id="",
        profile="default",
    )
    source._transport_adapter_ref = weakref.ref(adapter)
    hook_contexts = []

    def hook(_owner, hook_name, **kwargs):
        assert hook_name == "pre_user_message"
        hook_contexts.append(kwargs["context"])
        return [{
            "action": "handled",
            "handler": "project-main-binding",
            "response": "PROJECT MAIN READY",
        }]

    with patch("hermes_cli.lifecycle._plugin_hooks_for_owner", side_effect=hook):
        response = await runner._handle_message(
            MessageEvent(text="将当前对话设为项目 MAIN", source=source)
        )

    assert response == "PROJECT MAIN READY"
    assert len(hook_contexts) == 1
    assert is_core_stamped_context(hook_contexts[0])
    assert hook_contexts[0]["session_id"] == route.session_id
    assert hook_contexts[0]["profile_id"] == "default"


def test_cli_help_imports_the_patched_module():
    """The host patch must not leave an undefined mixin in cli.py."""
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, completed.stderr
    assert "synopsis" in output.lower()


def test_cli_inheritance_matches_the_post_collective_wisdom_baseline():
    """The upstream baseline removed the obsolete CLIWisdomMixin module."""
    from cli import HermesCLI

    assert "CLIWisdomMixin" not in {base.__name__ for base in HermesCLI.__mro__}
