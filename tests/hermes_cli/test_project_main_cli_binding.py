from pathlib import Path
from unittest.mock import patch

from cli import HermesCLI


def test_cli_accepts_explicit_host_workspace_root():
    with patch.object(HermesCLI, "_init_display_options"), patch.object(
        HermesCLI, "_init_model_routing"
    ), patch.object(HermesCLI, "_init_runtime_state"):
        cli = HermesCLI(workspace_root="C:/trusted/project")

    assert cli._project_main_session_workspace == "C:/trusted/project"


def test_cli_host_context_carries_the_active_profile_home():
    cli = object.__new__(HermesCLI)
    with patch.object(HermesCLI, "_init_session_store"), patch.object(
        HermesCLI, "_init_ui_state"
    ), patch("cli.get_hermes_home", return_value=Path("C:/hermes/profiles/project-sam2-hn-main")):
        cli._init_runtime_state(None)

    assert cli._project_main_host_context["profile_id"] == "project-sam2-hn-main"
    assert cli._project_main_host_context["profile_home"] == (
        "C:\\hermes\\profiles\\project-sam2-hn-main"
    )


def test_cli_chat_control_uses_host_context_before_agent_init():
    from hermes_cli.cli_chat_turn_mixin import CLIChatTurnMixin

    cli = object.__new__(CLIChatTurnMixin)
    cli._secret_capture_callback = lambda *_args, **_kwargs: None
    cli._agent_running = False
    with patch("cli.set_secret_capture_callback"), patch("cli._cprint"), patch(
        "hermes_cli.pre_user_message.core_context_from_cli_session",
        return_value={"host": "context"},
    ) as context_factory, patch(
        "hermes_cli.pre_user_message.dispatch_pre_user_message",
        return_value=(True, "handled"),
    ) as dispatch:
        result = cli.chat("将当前对话设为项目 MAIN")

    assert result == "handled"
    context_factory.assert_called_once_with(cli)
    assert dispatch.call_args.kwargs["context"] == {"host": "context"}
