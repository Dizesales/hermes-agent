"""Background and room producers retain source ownership across media and transcript lifetimes."""






import pytest



@pytest.mark.asyncio
async def test_automation_after_compression_and_released_media_uses_logical_owner(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority, SessionAuthority
    from gateway.session_ingress_context import native_callback
    from gateway.session_ingress_media import release_admission_media
    from gateway.session_automation import check_automation_route
    from hermes_constants import get_hermes_home
    from hermes_state_runtime import claim_session_input, settle_session_input, list_session_admissions
    from plugins.platforms.discord.adapter import DiscordAdapter
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    photo = tmp_path / 'photo.png'
    photo.write_bytes(b'photo')
    human = MessageEvent(text='human', source=source, message_id='human',
                         media_urls=[str(photo)], media_types=['image/png'])
    with native_callback(runner, human, get_hermes_home()):
        receipt = await authority.admit_native(human)
    sid = receipt.ref.session_id
    claim = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=receipt.admission_id,
                         generation=claim['generation'], outcome='completed')
    assert release_admission_media(authority.db, receipt.admission_id) == 1
    authority.db.publish_compression_child(parent_session_id=sid, child_session_id='child', source='discord',
        messages=[{'role': 'assistant', 'content': 'summary'}], require_compression_lease=False)
    route = runner.session_store._generate_session_key(source)
    runner.session_store.advance_compression_session(route, sid, 'child')
    event = MessageEvent(text='finished', source=source, internal=True,
        metadata={'gateway_session_key': route, 'gateway_session_id': sid})
    admitted = await authority.admit_automation(adapter, event, 'completion')
    assert admitted.ref.session_id == sid
    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
    assert check_automation_route(runner, rows[-1]['payload'], 'child', source, adapter)[1] == route
    assert (await authority.admit_automation(adapter, event, 'completion')).admission_id == admitted.admission_id


@pytest.mark.asyncio
async def test_automation_for_an_unserved_scope_never_dereferences_a_missing_authority(tmp_path, monkeypatch):
    """A multiplexed registry answers an unserved home's scope with None. The admitting authority
    still resolves its own compression tip, and a late route preflight refuses instead of crashing."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority, SessionAuthority
    from gateway.session_ingress_context import native_callback
    from gateway.session_automation import check_automation_route
    from hermes_constants import get_hermes_home
    from hermes_state_runtime import (RuntimeStoreError, claim_session_input, list_session_admissions,
                                      settle_session_input)
    from plugins.platforms.discord.adapter import DiscordAdapter
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    human = MessageEvent(text='hi', source=source, message_id='h')
    with native_callback(runner, human, get_hermes_home()):
        receipt = await authority.admit_native(human)
    sid = receipt.ref.session_id
    claim = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=receipt.admission_id,
                         generation=claim['generation'], outcome='completed')
    authority.db.publish_compression_child(parent_session_id=sid, child_session_id='child', source='discord',
        messages=[{'role': 'assistant', 'content': 'summary'}], require_compression_lease=False)
    route = runner.session_store._generate_session_key(source)
    runner.session_store.advance_compression_session(route, sid, 'child')
    # What SessionAuthorities.active answers for a scope this multiplexed runtime no longer serves.
    monkeypatch.setattr('gateway.session_authorities.active_authority', lambda runner: None)
    event = MessageEvent(text='finished', source=source, internal=True,
        metadata={'gateway_session_key': route, 'gateway_session_id': sid})
    assert (await authority.admit_automation(adapter, event, 'completion')).ref.session_id == sid
    row = list_session_admissions(authority.db, session_id=sid, pending_only=False)[-1]
    with pytest.raises(RuntimeStoreError, match='not_found'):
        check_automation_route(runner, row['payload'], 'child', source, adapter)


@pytest.mark.asyncio
async def test_automation_borrows_provenance_when_the_human_source_carries_its_message_id(monkeypatch):
    """Dize: Telegram topic sources carry the human message's id; the synthetic completion/watch
    event drops it (#52694) so the reply does not quote a stale message. The reply id is not route
    identity, so the borrowed source still matches and the automation is admitted, not refused
    ``admission_conflict`` and requeued forever."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority, SessionAuthority
    from gateway.session_identity import replace_source
    from gateway.session_ingress_context import native_callback
    from hermes_constants import get_hermes_home
    from hermes_state_runtime import (RuntimeStoreError, claim_session_input, list_session_admissions,
                                      settle_session_input)
    from plugins.platforms.discord.adapter import DiscordAdapter
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    source.message_id = '21844'
    human = MessageEvent(text='hi', source=source, message_id='21844')
    with native_callback(runner, human, get_hermes_home()):
        receipt = await authority.admit_native(human)
    sid = receipt.ref.session_id
    claim = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    settle_session_input(authority.db, epoch=authority.epoch, admission_id=receipt.admission_id,
                         generation=claim['generation'], outcome='completed')
    route = runner.session_store._generate_session_key(source)
    synthetic = replace_source(source, message_id=None)  # as _inject_watch_notification does
    event = MessageEvent(text='finished', source=synthetic, internal=True,
        metadata={'gateway_session_key': route, 'gateway_session_id': sid})
    admitted = await authority.admit_automation(adapter, event, 'completion')
    assert admitted.ref.session_id == sid
    row = list_session_admissions(authority.db, session_id=sid, pending_only=False)[-1]
    assert row['principal_id'] == 'automation:' + route
    assert 'message_id' not in row['payload']['native_text_v1']['source']  # no stale reply anchor
    from gateway.session_automation import check_automation_route
    restored_source, restored_route = check_automation_route(runner, row['payload'], sid, source, adapter)
    assert restored_route == route and restored_source.message_id is None
    # Any other source difference is still a conflict: only the reply id is ignored.
    stranger = MessageEvent(text='finished', source=replace_source(synthetic, user_id='43'), internal=True,
        metadata={'gateway_session_key': route, 'gateway_session_id': sid})
    with pytest.raises(RuntimeStoreError):
        await authority.admit_automation(adapter, stranger, 'completion-2')
