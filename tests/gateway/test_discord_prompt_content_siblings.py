"""Sibling coverage for self-contained Discord interactive prompts.

Slash-confirm and update prompts preserve their richer embeds. Clarify uses one
plain-text surface so the same question does not render twice on web/mobile.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter


def _capture_channel(adapter):
    sent = {}

    async def fake_send(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(id=1234)

    channel = SimpleNamespace(send=AsyncMock(side_effect=fake_send))
    adapter._client = SimpleNamespace(
        get_channel=lambda _chat_id: channel,
        fetch_channel=AsyncMock(),
        user=SimpleNamespace(
            display_name="Atena", global_name="Atena", name="Atena",
        ),
    )
    return sent


@pytest.mark.asyncio
async def test_slash_confirm_mirrors_message_into_content():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = _capture_channel(adapter)

    result = await adapter.send_slash_confirm(
        chat_id="555",
        title="Reset session?",
        message="This will clear the current conversation history.",
        session_key="discord:555",
        confirm_id="c1",
    )

    assert result.success is True
    assert sent["view"] is not None
    assert sent["embed"] is not None
    assert "Reset session?" in sent["content"]
    assert "clear the current conversation history" in sent["content"]


@pytest.mark.asyncio
async def test_slash_confirm_truncates_long_message_in_content():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = _capture_channel(adapter)

    result = await adapter.send_slash_confirm(
        chat_id="555",
        title="Confirm",
        message="y" * 5000,
        session_key="discord:555",
        confirm_id="c2",
    )

    assert result.success is True
    assert len(sent["content"]) <= adapter.MAX_MESSAGE_LENGTH
    assert "... [truncated]" in sent["content"]


@pytest.mark.asyncio
async def test_clarify_with_choices_uses_one_plain_text_surface():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = _capture_channel(adapter)

    result = await adapter.send_clarify(
        chat_id="555",
        question="Which environment should I deploy to?",
        choices=["staging", "production"],
        clarify_id="cl1",
        session_key="discord:555",
    )

    assert result.success is True
    assert sent["view"] is not None
    assert sent.get("embed") is None
    assert "Atena precisa da sua resposta" in sent["content"]
    assert "Which environment should I deploy to?" in sent["content"]
    assert "Escolha uma opção" in sent["content"]


@pytest.mark.asyncio
async def test_clarify_without_choices_mirrors_question_and_reply_hint():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = _capture_channel(adapter)

    result = await adapter.send_clarify(
        chat_id="555",
        question="What should the cron schedule be?",
        choices=[],
        clarify_id="cl2",
        session_key="discord:555",
    )

    assert result.success is True
    assert sent.get("view") is None
    assert sent.get("embed") is None
    assert "What should the cron schedule be?" in sent["content"]
    assert "Responda neste canal" in sent["content"]


@pytest.mark.asyncio
async def test_update_prompt_mirrors_prompt_into_content():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = _capture_channel(adapter)

    result = await adapter.send_update_prompt(
        chat_id="555",
        prompt="Restore stashed changes?",
        default="yes",
        session_key="discord:555",
    )

    assert result.success is True
    assert sent["view"] is not None
    assert "Update Needs Your Input" in sent["content"]
    assert "Restore stashed changes?" in sent["content"]
    assert "(default: yes)" in sent["content"]
