"""``react`` never renders a tool-progress bubble.

The emoji on the message IS the user-facing rendering of a reaction (as ``send_clarify`` is for
clarify, #52374), and a "react" bubble would add exactly the chat noise a reaction replaces.
"""

import sys

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from tests.gateway import test_clarify_progress_leak as leak


class ReactThenToolAgent(leak.ClarifyThenToolAgent):
    """Reacts, then runs a normal tool; returns once the progress task delivered its first bubble
    (FIFO: that bubble is the react one if react leaks, the terminal one if not)."""

    def run_conversation(self, message, conversation_history=None, task_id=None):
        self.tool_progress_callback("tool.started", "react", "❤️", {"emoji": "❤️"})
        self.tool_progress_callback("tool.started", "terminal", "pwd", {})
        if not type(self).adapter.delivered.wait(timeout=5.0):
            raise AssertionError("progress task never delivered a bubble")
        return {"final_response": "done", "messages": [], "api_calls": 1}


@pytest.mark.parametrize("mode", ["verbose", "all"])
@pytest.mark.asyncio
async def test_react_never_renders_a_progress_bubble(monkeypatch, tmp_path, mode):
    adapter = leak.ProgressCaptureAdapter(Platform.WHATSAPP)
    runner = leak._make_runner(adapter)
    monkeypatch.setattr(ReactThenToolAgent, "adapter", adapter)
    gateway_run = leak._install_fakes(monkeypatch, mode)
    monkeypatch.setattr(sys.modules["run_agent"], "AIAgent", ReactThenToolAgent)
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    result = await runner._run_agent(
        message="thanks!", context_prompt="", history=[],
        source=SessionSource(platform=Platform.WHATSAPP, chat_id="15551234567@s.whatsapp.net", chat_type="dm"),
        session_id="sess-react-quiet", session_key="agent:main:whatsapp:dm:15551234567@s.whatsapp.net",
    )

    assert result["final_response"] == "done"
    shown = "\n".join([m["content"] for m in adapter.sent] + [e["content"] for e in adapter.edits])
    assert "react" not in shown
    assert "❤️" not in shown
    # The unrelated terminal tool still renders, so the asserts above ran against a drained queue.
    assert "pwd" in shown
