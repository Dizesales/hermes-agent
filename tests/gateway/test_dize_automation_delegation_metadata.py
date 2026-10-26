"""Dize: async-delegation ids ride an automation admission and reach the replayed turn's metadata
(Hefesto plugins read them from gateway_event_context), without breaking admission validation."""
import pytest

from gateway.session_automation import dize_delegation_metadata
from hermes_state_runtime import RuntimeStoreError


def test_metadata_normalizes_and_omits_defaults():
    assert dize_delegation_metadata({}) == {}
    assert dize_delegation_metadata({'async_delegation_ids': []}) == {}
    assert dize_delegation_metadata({'async_delegation_ids': ['a', 'b', 'a'], 'async_delegation_interim': False}) == {
        'async_delegation_ids': ['a', 'b']}
    assert dize_delegation_metadata({'async_delegation_ids': ['a'], 'async_delegation_interim': True}) == {
        'async_delegation_ids': ['a'], 'async_delegation_interim': True}
    with pytest.raises(RuntimeStoreError):
        dize_delegation_metadata({'async_delegation_ids': ['ok', '']})


@pytest.mark.asyncio
async def test_completion_admission_persists_and_replays_delegation_ids(tmp_path, monkeypatch):
    from gateway.platforms.event import MessageEvent
    from gateway.session_envelope import restore_native
    from hermes_state_runtime import list_session_admissions
    from tests.gateway.test_automation_compression_media import _admit_human, _discord_owner
    from tests.gateway.test_completion_admission import pending

    runner, _, authority, source = await _discord_owner(tmp_path, monkeypatch)
    receipt = await _admit_human(runner, authority, MessageEvent(text='human', source=source, message_id='human'))
    key = runner.session_store._generate_session_key(source)
    root = receipt.ref.session_id
    assert await runner._deliver_async_delegation_group([dict(pending(key, 'deleg-1'), parent_session_id=root)]) is True
    row = list_session_admissions(authority.db, session_id=root, pending_only=False)[-1]
    automation = row['payload']['native_text_v1']['automation']
    assert automation['async_delegation_ids'] == ['deleg-1']
    assert 'async_delegation_interim' not in automation
    replayed = restore_native(row['payload'], runner)
    assert replayed.metadata['async_delegation_ids'] == ['deleg-1']
