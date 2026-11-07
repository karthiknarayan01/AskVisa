"""The agent's setup: which tools it gets, and the options every session gets."""

from runtime.agent import Agent, agent_tools, build_agent
from runtime.approval import DenyAll
from runtime.tools import ToolRegistry
from tests.fake_model import FakeModel


def names(tools):
    return {t.name for t in tools}


def test_without_a_service_key_the_keyless_tools_of_every_group(monkeypatch):
    monkeypatch.delenv("PERMTRACKER_API_KEY", raising=False)
    tools = names(agent_tools())
    assert {"match_employer", "find_soc_code", "immigration_data_page", "read_regulation",
            "search_federal_register", "grace_period_end", "date_math", "web_search"} <= tools
    assert "perm_case_status" not in tools


def test_with_a_service_key_every_perm_tool(monkeypatch):
    monkeypatch.setenv("PERMTRACKER_API_KEY", "pt_test")
    assert {"perm_case_status", "visa_bulletin", "export_search"} <= names(agent_tools())


def test_web_can_be_left_out(monkeypatch):
    monkeypatch.delenv("PERMTRACKER_API_KEY", raising=False)
    assert not {"web_search", "read_web_page"} & names(agent_tools(web=False))


def test_session_options_prompt_for_the_tools_present_and_deny_approvals():
    agent = Agent("anthropic", "m", FakeModel([]), ToolRegistry(agent_tools(web=False)))
    options = agent.session_options()
    assert isinstance(options["approver"], DenyAll)
    assert "match_employer" in options["system_prompt"] and "web_search" not in options["system_prompt"]
    assert "compaction" not in options


def test_a_model_with_a_known_window_gets_a_matching_compaction_policy():
    model = FakeModel([])
    model.context_window = 32_768
    options = Agent("ollama", "qwen3.5:9b", model, ToolRegistry([])).session_options()
    assert options["compaction"].context_window == 32_768


async def test_only_a_local_model_is_checked():
    class Checked(FakeModel):
        checked = False

        async def check(self):
            Checked.checked = True

    await Agent("anthropic", "m", Checked([]), ToolRegistry([])).check()
    assert not Checked.checked
    await Agent("ollama", "m", Checked([]), ToolRegistry([])).check()
    assert Checked.checked


def test_build_agent_uses_the_providers_default_model(monkeypatch):
    agent = build_agent("ollama", web=False)
    assert (agent.provider, agent.model_name) == ("ollama", "qwen3.5:9b")
