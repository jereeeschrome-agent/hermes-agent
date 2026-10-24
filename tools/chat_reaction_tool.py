#!/usr/bin/env python3
"""Native emoji reaction on the chat message a messaging turn answers: the gateway counterpart of the
desktop ``react_to_message``. The ``chat_reactions`` toolset is session-gated to platforms whose adapter
implements ``add_reaction``/``remove_reaction`` (``toolsets.py``), so it costs nothing elsewhere and is
never deferred behind tool_search: like clarify, a reaction only gets used when it is ambient. A reaction
that answers the turn lets a silence marker stand (``gateway/run_turn_persistence.py::_turn_silence_allowed``)."""

import json

from gateway.session_context import get_session_env, turn_delivery_adapter
from tools.registry import registry, tool_error

# Longest ZWJ sequences (families, couples with skin tones) stay under this many code points.
_MAX_EMOJI_CODEPOINTS = 16


def _is_single_emoji(text: str) -> bool:
    """Cheap guard, not the emoji grammar: rejects words (``heart``), ASCII art (``<3``) and sentences."""
    return (not text.isascii() and len(text) <= _MAX_EMOJI_CODEPOINTS
            and not any(c.isspace() or (c.isascii() and c.isalpha()) for c in text))


def react_tool(emoji: str) -> str:
    """React to (with ``emoji``) or, with ``""``, un-react from the message that triggered this turn."""
    emoji = (emoji or "").strip()
    if emoji and not _is_single_emoji(emoji):
        return tool_error("emoji must be one emoji such as ❤️, 👍 or 😂 (an empty string removes your reaction).")
    platform_name = get_session_env("HERMES_SESSION_PLATFORM", "")
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
    message_id = get_session_env("HERMES_SESSION_MESSAGE_ID", "")
    if not (platform_name and chat_id and message_id):
        return tool_error("This turn has no chat message to react to.")
    # The bot that answers THIS turn, bound by the gateway when the turn started: with a shared bot
    # routing the chat to this profile, a lookup by the active profile finds no adapter at all.
    react = getattr(turn_delivery_adapter(), "add_reaction" if emoji else "remove_reaction", None)
    if not callable(react):
        return tool_error(f"Reactions are not available on {platform_name} right now.")
    kwargs = {"chat_id": chat_id, "message_id": message_id, **({"emoji": emoji} if emoji else {})}
    from gateway.run import _gateway_runner_ref
    from model_tools import _run_async
    from tools.send_message_tool import _dispatch_on_gateway_loop
    # The adapter's HTTP session belongs to the gateway loop; awaiting it from the tool's worker
    # loop fails, so the call is dispatched there. Exceptions surface through the registry.
    result = _run_async(_dispatch_on_gateway_loop(
        _gateway_runner_ref(), lambda: react(**kwargs), "react: failed to schedule on the gateway loop"))
    if not (isinstance(result, dict) and result.get("success")):
        detail = result.get("error") if isinstance(result, dict) else None
        return tool_error(f"Reaction failed: {detail or 'unknown error'}")
    if not emoji:
        return json.dumps({"success": True, "removed": True})
    return json.dumps({
        "success": True, "reacted": emoji,
        "note": "If this reaction is your whole reply, end your turn with exactly NO_REPLY.",
    }, ensure_ascii=False)


REACT_SCHEMA = {
    "name": "react",
    "description": (
        "React with an emoji to the chat message you are answering. When the message needs no "
        "answer (a thank-you, an 'ok' or 'will do', a good night, a joke, a bare emoji), react "
        "INSTEAD of replying: call react with a fitting emoji (❤️, 👍, 😂, 🙏), then end your turn "
        "with exactly NO_REPLY so no text is sent. A 'you're welcome!' or 'sounds good!' after a "
        "reaction is the noise this replaces. When the message needs an answer, reply as usual; "
        "reacting as well is optional. Never narrate a reaction ('I reacted with...') and never use "
        "one as a status signal. One reaction per message: a different emoji replaces yours, an "
        "empty string removes it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "description": (
                    "The emoji to react with (e.g. '❤️', '👍', '😂'). Pass an empty string to remove "
                    "your reaction."
                ),
            },
        },
        "required": ["emoji"],
    },
}


registry.register(
    name="react", toolset="chat_reactions", schema=REACT_SCHEMA,
    handler=lambda args, **kw: react_tool(emoji=args.get("emoji", "")), emoji="💛",
)
