from unittest.mock import patch

from cli import HermesCLI


def test_cli_accepts_explicit_host_workspace_root():
    with patch.object(HermesCLI, "_init_display_options"), patch.object(
        HermesCLI, "_init_model_routing"
    ), patch.object(HermesCLI, "_init_runtime_state"):
        cli = HermesCLI(workspace_root="C:/trusted/project")

    assert cli._project_main_session_workspace == "C:/trusted/project"
