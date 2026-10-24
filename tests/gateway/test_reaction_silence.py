"""A turn the agent answered with a reaction may end on a bare silence marker.

A human turn normally gets a visible fallback for ``NO_REPLY`` (the user must see something), but
an agent reaction on the message being answered IS something the user sees — that is the whole
point of reacting instead of replying. The delivery adapter reports its reactions
(``agent_reaction``); any other reaction state keeps the fallback.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent
from gateway.response_filters import is_intentional_silence_response
from gateway.session import SessionSource
from tests.gateway.test_gateway_silence_tokens import _runner

CHAT = "120363000000000000@g.us"
KEY = "agent:main:whatsapp:group:120363000000000000@g.us"


class _Reactions:
    """Delivery adapter stand-in: the messages carrying our reaction (``send`` takes the turn's
    platform notices)."""

    def __init__(self, *reacted):
        self.reacted = set(reacted)

    def agent_reaction(self, chat_id, message_id):
        return "❤️" if (chat_id, message_id) in self.reacted else None

    async def send(self, chat_id, content, **_kwargs):
        return SimpleNamespace(success=True, message_id=None)


def _source(message_id="MSG1"):
    return SessionSource(platform=Platform.WHATSAPP, chat_id=CHAT, chat_type="group",
                         user_id="15551234567@s.whatsapp.net", message_id=message_id)


def _silent_result(**extra):
    return {
        "final_response": "NO_REPLY", "tools": [], "history_offset": 0, "last_prompt_tokens": 0,
        "api_calls": 2, "failed": False,
        "messages": [{"role": "user", "content": "thanks!"}, {"role": "assistant", "content": "NO_REPLY"}],
        **extra,
    }


async def _answer(monkeypatch, tmp_path, adapter, result):
    runner = _runner(monkeypatch, tmp_path)
    runner._delivery_adapter_for = lambda _source: adapter
    runner._run_agent = AsyncMock(return_value=result)
    event = MessageEvent(text="thanks!", source=_source(), message_id="MSG1")
    return await runner._handle_message_with_agent(event, _source(), KEY, 1)


@pytest.mark.asyncio
async def test_reaction_on_the_answered_message_lets_the_silence_marker_stand(monkeypatch, tmp_path):
    assert await _answer(monkeypatch, tmp_path, _Reactions((CHAT, "MSG1")), _silent_result()) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [_Reactions(), _Reactions((CHAT, "OTHER")), None],
                         ids=["no-reaction", "reaction-elsewhere", "no-adapter"])
async def test_without_a_reaction_on_it_the_turn_still_gets_the_fallback(monkeypatch, tmp_path, adapter):
    response = await _answer(monkeypatch, tmp_path, adapter, _silent_result())

    assert response and not is_intentional_silence_response(response)


@pytest.mark.asyncio
async def test_queued_chain_is_judged_by_its_terminal_message(monkeypatch, tmp_path):
    """The last message of a /queue chain owns the verdict: a reaction on the opener does not
    answer it, a reaction on the terminal message does."""
    chain = _silent_result(queued_terminal_inbound_id="MSG2")

    response = await _answer(monkeypatch, tmp_path, _Reactions((CHAT, "MSG1")), chain)
    assert response and not is_intentional_silence_response(response)

    assert await _answer(monkeypatch, tmp_path, _Reactions((CHAT, "MSG2")), chain) == ""


def test_queued_follow_up_turn_targets_its_own_message(monkeypatch):
    """A follow-up runs inside the turn that opened the chain, whose session env names the opener;
    the follow-up's tools (``react``) must see the follow-up's message, without the rebinding
    leaking back into the opening turn."""
    from contextvars import copy_context

    from gateway.run_turn_runner import TurnRunner
    from gateway.session_context import clear_session_vars, get_session_env, set_session_vars

    seen = {}

    def _turn_body(self):
        seen["message_id"] = get_session_env("HERMES_SESSION_MESSAGE_ID")
        return {"final_response": ""}

    monkeypatch.setattr(TurnRunner, "_run_sync_scoped", _turn_body)
    tokens = set_session_vars(platform="whatsapp", chat_id=CHAT, message_id="OPENER")
    try:
        ctx = SimpleNamespace(source=_source("FOLLOW-UP"), session_id="sid", session_key=KEY, _interrupt_depth=1)
        copy_context().run(TurnRunner(SimpleNamespace(session_authority=None), ctx).run_sync)
        assert seen["message_id"] == "FOLLOW-UP"
        assert get_session_env("HERMES_SESSION_MESSAGE_ID") == "OPENER"
    finally:
        clear_session_vars(tokens)


def test_turn_binds_the_bot_that_answers_it(monkeypatch):
    """Tools acting on the turn's own chat (``react``) reach the adapter the gateway answers this
    source with — the receiving bot, also when a shared bot routes the chat to another profile —
    bound in the turn's own context only."""
    from contextvars import copy_context

    from gateway.run_turn_runner import TurnRunner
    from gateway.session_context import turn_delivery_adapter

    answering, seen = _Reactions(), {}

    def _turn_body(self):
        seen["adapter"] = turn_delivery_adapter()
        return {"final_response": ""}

    monkeypatch.setattr(TurnRunner, "_run_sync_scoped", _turn_body)
    runner = SimpleNamespace(
        session_authority=None,
        _delivery_adapter_for=lambda source: answering if source.chat_id == CHAT else None)
    ctx = SimpleNamespace(source=_source(), session_id="sid", session_key=KEY, _interrupt_depth=0)
    copy_context().run(TurnRunner(runner, ctx).run_sync)

    assert seen["adapter"] is answering
    assert turn_delivery_adapter() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reacted, silent", [(True, True), (False, False)])
async def test_queued_first_response_honours_the_reaction(reacted, silent):
    runner = gateway_run.GatewayRunner(GatewayConfig())
    runner._deliver_queued_first_response = AsyncMock()
    runner._delivery_adapter_for = lambda _source: _Reactions(*([(CHAT, "MSG1")] if reacted else []))
    turn_ctx = SimpleNamespace(
        session_key=KEY, stream_consumer_holder=[None], mute_notification_reply=False,
        persist_user_display_kind=None, reply_expected=None, source=_source(),
        _status_thread_metadata=None, event_message_id=None, inbound_message_id="MSG1", run_generation=1)
    result = {"final_response": "NO_REPLY", "failed": False}

    await runner._run_agent_deliver_first_response(turn_ctx, None, result, result, None)

    delivered = runner._deliver_queued_first_response.await_args
    if silent:
        assert delivered is None or not delivered.args[0]
    else:
        assert delivered.args[0] and not is_intentional_silence_response(delivered.args[0])
