"""The ``react`` tool: a native emoji reaction on the chat message a messaging turn answers.

Covers the handler through the registry (target = this turn's message and bot, removal, refusals),
the gateway-loop dispatch the adapter's HTTP session needs, and the session gating that keeps the
tool on WhatsApp sessions only, ambient (never deferred) and away from subagents.
"""

import asyncio
import json
import threading
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import model_tools
import toolsets
from gateway.session_context import bind_turn_delivery_adapter, clear_session_vars, set_session_vars
from tools.registry import registry

GROUP = "120363000000000000@g.us"


class _ReactingAdapter:
    """Adapter exposing the add_reaction/remove_reaction contract; records each call and its loop."""

    def __init__(self, result=None):
        self.calls, self.loops, self._result = [], [], result

    async def add_reaction(self, chat_id, emoji, message_id=None):
        self.calls.append(("add", chat_id, message_id, emoji))
        self.loops.append(asyncio.get_running_loop())
        return self._result or {"success": True, "message_id": message_id, "emoji": emoji}

    async def remove_reaction(self, chat_id, message_id=None):
        self.calls.append(("remove", chat_id, message_id))
        self.loops.append(asyncio.get_running_loop())
        return self._result or {"success": True, "message_id": message_id}


class _NoReactionsAdapter:
    """An adapter that cannot react."""


@pytest.fixture
def whatsapp_turn():
    """Session env of a WhatsApp group turn triggered by message MSG1."""
    tokens = set_session_vars(platform="whatsapp", chat_id=GROUP, chat_type="group", message_id="MSG1")
    yield
    clear_session_vars(tokens)


def _react(emoji, adapter=None, *, loop=None, runner=None):
    """Dispatch ``react`` inside a turn answered by *adapter* (bound as the gateway binds it)."""
    def _in_turn():
        bind_turn_delivery_adapter(adapter)
        return json.loads(registry.dispatch("react", {"emoji": emoji}))

    with patch("gateway.run._gateway_runner_ref", lambda: runner or SimpleNamespace(_gateway_loop=loop)):
        return copy_context().run(_in_turn)


def test_reacts_to_the_message_this_turn_answers(whatsapp_turn):
    adapter = _ReactingAdapter()
    result = _react("❤️", adapter)

    assert adapter.calls == [("add", GROUP, "MSG1", "❤️")]
    assert result["success"] is True and result["reacted"] == "❤️"
    assert "NO_REPLY" in result["note"]


def test_reacts_through_the_bot_answering_the_turn_when_a_shared_bot_routes_the_chat(whatsapp_turn):
    """``profile_routes`` can hand a chat on the default profile's WhatsApp bot to another profile
    that owns no WhatsApp adapter; a lookup by the active profile then finds nothing. The bot
    answering the turn is the one that reacts (live regression)."""
    adapter = _ReactingAdapter()
    routed_runner = SimpleNamespace(adapters={}, _gateway_loop=None, _authorization_adapter=lambda *_a: None)

    result = _react("👍", adapter, runner=routed_runner)

    assert result["success"] is True
    assert adapter.calls == [("add", GROUP, "MSG1", "👍")]


def test_empty_emoji_removes_our_reaction(whatsapp_turn):
    adapter = _ReactingAdapter()
    result = _react("", adapter)

    assert adapter.calls == [("remove", GROUP, "MSG1")]
    assert result == {"success": True, "removed": True}


@pytest.mark.parametrize("text", ["heart", ":+1:", "<3", "thanks 👍", "👍 👍"])
def test_non_emoji_text_is_refused_before_it_reaches_the_chat(whatsapp_turn, text):
    adapter = _ReactingAdapter()
    result = _react(text, adapter)

    assert "error" in result
    assert adapter.calls == []


@pytest.mark.parametrize("emoji", ["👍🏽", "👨‍👩‍👧‍👦", "🇳🇱", "1️⃣", "❤️‍🔥"])
def test_composed_emoji_are_accepted(whatsapp_turn, emoji):
    result = _react(emoji, _ReactingAdapter())

    assert result.get("success") is True, result


def test_turn_without_a_triggering_message_has_nothing_to_react_to():
    tokens = set_session_vars(platform="whatsapp", chat_id=GROUP)
    try:
        adapter = _ReactingAdapter()
        result = _react("👍", adapter)
    finally:
        clear_session_vars(tokens)

    assert "error" in result
    assert adapter.calls == []


@pytest.mark.parametrize("adapter", [None, _NoReactionsAdapter()], ids=["outside-a-turn", "adapter-cannot-react"])
def test_no_reacting_adapter_is_an_error(whatsapp_turn, adapter):
    result = _react("👍", adapter)

    assert "not available" in result["error"]


def test_adapter_failure_reaches_the_model(whatsapp_turn):
    result = _react("👍", _ReactingAdapter(result={"success": False, "error": "message MSG1 is not in chat"}))

    assert "not in chat" in result["error"]


def test_reaction_runs_on_the_gateway_loop(whatsapp_turn):
    """The WhatsApp adapter's aiohttp session belongs to the gateway loop; awaiting it from the
    tool's worker loop breaks, so the call must be dispatched onto the gateway loop."""
    gateway_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=gateway_loop.run_forever, daemon=True)
    thread.start()
    adapter = _ReactingAdapter()
    try:
        result = _react("👍", adapter, loop=gateway_loop)
    finally:
        gateway_loop.call_soon_threadsafe(gateway_loop.stop)
        thread.join(timeout=5)
        gateway_loop.close()

    assert result["success"] is True
    assert adapter.loops == [gateway_loop]


def _session_tool_names(platform, config=None):
    from hermes_cli.tools_config import _get_platform_tools

    enabled = sorted(_get_platform_tools(config or {}, platform))
    defs = model_tools.get_tool_definitions(
        enabled_toolsets=enabled, disabled_toolsets=toolsets.session_disabled_toolsets(None, platform),
        quiet_mode=True)
    return {d["function"]["name"] for d in defs}


def test_only_whatsapp_sessions_carry_the_tool():
    assert "react" in _session_tool_names("whatsapp")
    # A customized toolset list keeps platform-native tools, as it does for Discord's.
    assert "react" in _session_tool_names("whatsapp", {"platform_toolsets": {"whatsapp": ["web", "file"]}})
    for platform in ("telegram", "cli"):
        assert "react" not in _session_tool_names(platform), platform


def test_the_tool_is_ambient_never_deferred_behind_tool_search():
    from tools.tool_search import is_deferrable_tool_name

    assert not is_deferrable_tool_name("react")


def test_subagents_never_react_in_the_users_chat():
    from tools.delegate_tool_toolsets import _resolve_child_toolsets

    parent = SimpleNamespace(enabled_toolsets=["hermes-whatsapp"], disabled_toolsets=[])
    enabled, disabled = _resolve_child_toolsets(parent, None, "leaf")
    names = {d["function"]["name"] for d in model_tools.get_tool_definitions(
        enabled_toolsets=enabled, disabled_toolsets=disabled, quiet_mode=True)}

    assert "react" not in names
