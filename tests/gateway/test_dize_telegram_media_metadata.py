"""Dize: Hefesto's Telegram media references cross native admission under a closed key set."""

from datetime import datetime

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session import SessionSource
from gateway.session_envelope import _native_metadata


def _event(platform, metadata):
    source = SessionSource(platform=platform, chat_id="-100123", chat_type="group", user_id="42", thread_id="7")
    return MessageEvent(text="legenda", source=source, timestamp=datetime.now(),
                        message_type=MessageType.PHOTO, metadata=metadata)


DOCUMENT = {"file_id": "AgAC", "file_unique_id": "AQAD", "filename": "photo.jpg",
            "mime_type": "image/jpeg", "file_size": 1234, "telegram_kind": "photo"}


def test_hefesto_media_metadata_is_projected_for_telegram():
    metadata = {"hefesto_telegram_document": DOCUMENT,
                "hefesto_native_batch": {"kind": "photo", "key": "ab" * 32}}
    assert _native_metadata(_event(Platform.TELEGRAM, metadata)) == metadata


def test_album_and_recovered_documents_are_projected():
    metadata = {"hefesto_telegram_media": [DOCUMENT, DOCUMENT],
                "hefesto_recover_documents": [{"document": DOCUMENT}]}
    assert _native_metadata(_event(Platform.TELEGRAM, metadata)) == metadata


@pytest.mark.parametrize("metadata", [
    {"hefesto_telegram_document": DOCUMENT, "gateway_session_key": "forged"},  # key outside the set
    {"hefesto_telegram_document": {"file_id": object()}},                     # not JSON
    {"hefesto_telegram_media": ["x" * (70 * 1024)]},                          # over the size cap
])
def test_anything_else_still_refuses(metadata):
    assert _native_metadata(_event(Platform.TELEGRAM, metadata)) is None


def test_hefesto_keys_do_not_open_other_platforms():
    assert _native_metadata(_event(Platform.DISCORD, {"hefesto_telegram_document": DOCUMENT})) is None


def test_event_without_metadata_is_unchanged():
    assert _native_metadata(_event(Platform.TELEGRAM, {})) == {}
