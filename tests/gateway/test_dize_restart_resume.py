"""Dize: chats a planned restart cut mid-turn get one resume turn on their FIFO (opt-in)."""

import pytest


async def _cut_turn(monkeypatch, interrupt_message):
    """A Discord DM turn admitted by the first owner and settled ``interrupted`` by its shutdown."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority, SessionAuthority
    from gateway.session_ingress_context import native_callback
    from hermes_constants import get_hermes_home
    from hermes_state_runtime import claim_session_input, settle_session_input
    from plugins.platforms.discord.adapter import DiscordAdapter
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    first = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    human = MessageEvent(text='faz o deploy', source=source, message_id='h')
    with native_callback(runner, human, get_hermes_home()):
        receipt = await first.admit_native(human)
    sid = receipt.ref.session_id
    claim = claim_session_input(first.db, epoch=first.epoch, session_id=sid)
    settle_session_input(first.db, epoch=first.epoch, admission_id=receipt.admission_id,
                         generation=claim['generation'], outcome='interrupted',
                         result={'result': {'final_response': '', 'interrupted': True,
                                            'interrupt_message': interrupt_message}, 'usage': {}})
    route = runner.session_store._generate_session_key(source)
    runner.session_store.get_or_create_session(source)
    restarted = await initialize_session_authority(runner, profile_id='default', instance_id='restart')
    assert restarted.epoch == first.epoch + 1
    return runner, restarted, sid, route


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,guidance', [('ask', 'ask what they would like to do next'),
                                           ('continue', 'CONTINUE the interrupted task')])
async def test_restart_cut_turn_gets_one_resume_turn(monkeypatch, mode, guidance):
    from gateway.dize_lost_turn_recovery import resume_restart_interrupted_turns
    from hermes_state_runtime import list_session_admissions
    monkeypatch.setenv('HERMES_DIZE_RESUME_INTERRUPTED_TURNS', mode)
    runner, authority, sid, route = await _cut_turn(monkeypatch, 'Gateway shutting down')
    assert await resume_restart_interrupted_turns(runner, authority) == [sid]
    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
    assert [(r['status'], r['principal_id']) for r in rows] == [
        ('terminal', rows[0]['principal_id']), ('queued', 'automation:' + route)]
    text = rows[-1]['payload']['text']
    assert 'interrupted by a gateway restart' in text and guidance in text
    # Once resumed, the cut turn is no longer the newest: a second pass does nothing.
    assert await resume_restart_interrupted_turns(runner, authority) == []


@pytest.mark.asyncio
async def test_a_user_stop_is_not_resumed(monkeypatch):
    from gateway.dize_lost_turn_recovery import resume_restart_interrupted_turns
    monkeypatch.setenv('HERMES_DIZE_RESUME_INTERRUPTED_TURNS', 'continue')
    runner, authority, _, _ = await _cut_turn(monkeypatch, 'Stopped by user')
    assert await resume_restart_interrupted_turns(runner, authority) == []


@pytest.mark.asyncio
async def test_disabled_by_default(monkeypatch):
    from gateway.dize_lost_turn_recovery import resume_restart_interrupted_turns
    from hermes_state_runtime import list_session_admissions
    monkeypatch.delenv('HERMES_DIZE_RESUME_INTERRUPTED_TURNS', raising=False)
    runner, authority, sid, _ = await _cut_turn(monkeypatch, 'Gateway shutting down')
    assert await resume_restart_interrupted_turns(runner, authority) == []
    assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == 1
