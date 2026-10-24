"""WhatsApp (Baileys bridge) agent reactions: the adapter's add/remove contract over the bridge's
``/react`` endpoint, and the reaction state the gateway reads to let a reacted-to turn stay silent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.gateway.test_whatsapp_formatting import _AsyncCM, _make_adapter

GROUP = "120363000000000000@g.us"


def _bridge(adapter, *, status=200, error_text=""):
    """Point the adapter's HTTP session at a fake bridge; returns the ``post`` mock."""
    resp = MagicMock(status=status)
    resp.json = AsyncMock(return_value={"success": True, "messageId": "REACTION-MSG"})
    resp.text = AsyncMock(return_value=error_text)
    adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))
    return adapter._http_session.post


@pytest.mark.asyncio
async def test_group_reaction_posts_to_the_bridge_and_is_remembered():
    adapter = _make_adapter()
    post = _bridge(adapter)

    result = await adapter.add_reaction(GROUP, "❤️", message_id="MSG1")

    assert result == {"success": True, "message_id": "MSG1", "emoji": "❤️"}
    call = post.call_args
    assert call.args[0] == "http://127.0.0.1:3000/react"
    assert call.kwargs["json"] == {"chatId": GROUP, "messageId": "MSG1", "emoji": "❤️"}
    assert adapter.agent_reaction(GROUP, "MSG1") == "❤️"
    assert adapter.agent_reaction(GROUP, "OTHER") is None


@pytest.mark.asyncio
async def test_dm_phone_number_is_sent_as_a_jid():
    adapter = _make_adapter()
    post = _bridge(adapter)

    await adapter.add_reaction("15551234567", "👍", message_id="MSG2")

    assert post.call_args.kwargs["json"]["chatId"] == "15551234567@s.whatsapp.net"
    assert adapter.agent_reaction("15551234567@s.whatsapp.net", "MSG2") == "👍"


@pytest.mark.asyncio
async def test_a_new_emoji_replaces_ours_and_removal_clears_it():
    adapter = _make_adapter()
    post = _bridge(adapter)

    await adapter.add_reaction(GROUP, "❤️", message_id="MSG1")
    await adapter.add_reaction(GROUP, "😂", message_id="MSG1")
    assert adapter.agent_reaction(GROUP, "MSG1") == "😂"

    result = await adapter.remove_reaction(GROUP, message_id="MSG1")

    assert result["success"] is True
    assert post.call_args.kwargs["json"] == {"chatId": GROUP, "messageId": "MSG1", "emoji": ""}
    assert adapter.agent_reaction(GROUP, "MSG1") is None


@pytest.mark.asyncio
async def test_bridge_refusal_is_reported_and_not_remembered():
    adapter = _make_adapter()
    _bridge(adapter, status=400, error_text='{"error":"message MSG1 is not in chat"}')

    result = await adapter.add_reaction(GROUP, "👍", message_id="MSG1")

    assert result["success"] is False
    assert "not in chat" in result["error"]
    assert adapter.agent_reaction(GROUP, "MSG1") is None


@pytest.mark.asyncio
async def test_reaction_needs_a_message_id():
    adapter = _make_adapter()
    post = _bridge(adapter)

    result = await adapter.add_reaction(GROUP, "👍")

    assert result["success"] is False
    assert "message_id" in result["error"]
    post.assert_not_called()


@pytest.mark.asyncio
async def test_disconnected_adapter_does_not_touch_the_bridge():
    adapter = _make_adapter()
    post = _bridge(adapter)
    adapter._running = False

    result = await adapter.add_reaction(GROUP, "👍", message_id="MSG1")

    assert result == {"success": False, "error": "Not connected"}
    post.assert_not_called()
