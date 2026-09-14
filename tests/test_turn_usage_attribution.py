from concurrent.futures import ThreadPoolExecutor
import sqlite3
from hermes_state import SessionDB


def test_queued_turn_projection_and_absolute_snapshot(tmp_path):
    path = tmp_path / 'state.db'
    db = SessionDB(db_path=path)
    try:
        def write(i):
            db.queue_token_counts('fixture', turn_id=f'turn-{i % 2}',
                                  input_tokens=10, output_tokens=2,
                                  cache_read_tokens=90, api_call_count=1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(16)))
        assert db.flush_token_counts()
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT input_tokens, output_tokens, cache_read_tokens, api_call_count FROM session_turn_usage ORDER BY turn_id').fetchall() == [(80, 16, 720, 8)] * 2
        db.update_token_counts('fixture', turn_id='turn-0', absolute=True,
                               input_tokens=160, output_tokens=32,
                               cache_read_tokens=1440, api_call_count=16)
        db.update_token_counts('fixture', input_tokens=5)
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT SUM(input_tokens) FROM session_turn_usage').fetchone()[0] == 160
            assert c.execute('SELECT input_tokens FROM sessions').fetchone()[0] == 165
            c.execute('PRAGMA foreign_keys=ON')
            c.execute("DELETE FROM sessions WHERE id='fixture'")
            assert c.execute('SELECT COUNT(*) FROM session_turn_usage').fetchone()[0] == 0
    finally:
        db.close()


def test_projection_failure_rolls_back_aggregate(tmp_path):
    path = tmp_path / 'state.db'
    db = SessionDB(db_path=path)
    try:
        db.update_token_counts('fixture', input_tokens=5)
        with sqlite3.connect(path) as c:
            c.execute("CREATE TRIGGER reject_turn BEFORE INSERT ON session_turn_usage BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            db.update_token_counts('fixture', turn_id='turn', input_tokens=10)
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT input_tokens FROM sessions').fetchone()[0] == 5
    finally:
        db.close()


def test_batch_never_merges_distinct_turns(tmp_path):
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        batch = [('s', dict(turn_id=t, input_tokens=1)) for t in ('a', 'b', 'b')]
        merged = db._coalesce_token_deltas(batch)
        assert [(kw['turn_id'], kw['input_tokens']) for _, kw in merged] == [('a', 1), ('b', 2)]
    finally:
        db.close()


def test_reader_flushes_and_isolates_sessions(tmp_path):
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        assert db.get_turn_usage('a', 'turn') is None
        db.queue_token_counts('a', turn_id='turn', input_tokens=7, api_call_count=1)
        db.queue_token_counts('b', turn_id='turn', input_tokens=11, api_call_count=1)
        assert db.get_turn_usage('a', 'turn')['input_tokens'] == 7
        assert db.get_turn_usage('b', 'turn')['input_tokens'] == 11
    finally:
        db.close()


def test_reader_refuses_undrained_queue(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / 'state.db')
    try:
        monkeypatch.setattr(db, 'flush_token_counts', lambda: False)
        import pytest
        with pytest.raises(TimeoutError):
            db.get_turn_usage('a', 'turn')
    finally:
        monkeypatch.undo()
        db.close()


def test_existing_database_additive_upgrade(tmp_path):
    # Existing schema without the optional projection; preserve historical totals.
    path = tmp_path / 'state.db'
    db = SessionDB(db_path=path)
    db.update_token_counts('old-session', input_tokens=123, output_tokens=45)
    db.close()
    with sqlite3.connect(path) as c:
        c.execute('DROP TABLE session_turn_usage')
    db = SessionDB(db_path=path)
    try:
        assert db.get_session('old-session')['input_tokens'] == 123
        assert db.get_turn_usage('old-session', 'old-turn') is None
        db.queue_token_counts('old-session', turn_id='new-turn', input_tokens=7)
        assert db.get_turn_usage('old-session', 'new-turn')['input_tokens'] == 7
        assert db.get_session('old-session')['input_tokens'] == 130
    finally:
        db.close()
    reader = SessionDB(db_path=path, read_only=True)
    try:
        assert reader.get_turn_usage('old-session', 'new-turn')['input_tokens'] == 7
    finally:
        reader.close()


def test_codex_accounting_reaches_real_turn_reader(tmp_path):
    from types import SimpleNamespace
    from agent.codex_runtime import _record_codex_app_server_usage
    db = SessionDB(db_path=tmp_path / 'state.db')
    agent = SimpleNamespace(_session_db=db, _session_db_created=True,
        session_id='codex-session', _current_turn_id='hermes-turn',
        model='fixture-model', provider='openai-codex', base_url='',
        context_compressor=None, session_api_calls=0,
        session_prompt_tokens=0, session_completion_tokens=0,
        session_total_tokens=0, session_input_tokens=0, session_output_tokens=0,
        session_cache_read_tokens=0, session_cache_write_tokens=0,
        session_reasoning_tokens=0, session_estimated_cost_usd=0)
    try:
        turn = SimpleNamespace(token_usage_last=dict(inputTokens=20,
            cachedInputTokens=80, outputTokens=10, reasoningOutputTokens=4))
        _record_codex_app_server_usage(agent, turn)
        usage = db.get_turn_usage('codex-session', 'hermes-turn')
        assert usage['input_tokens'] == agent.session_input_tokens == 20
        assert usage['cache_read_tokens'] == 80
        assert usage['output_tokens'] == 10
        agent._current_turn_id = 'missing-usage'
        _record_codex_app_server_usage(agent, SimpleNamespace(token_usage_last=None))
        assert db.get_turn_usage('codex-session', 'missing-usage') is None
    finally:
        db.close()


def test_additive_future_column_does_not_break_accounting(tmp_path):
    path = tmp_path / 'state.db'
    db = SessionDB(db_path=path)
    try:
        with sqlite3.connect(path) as c:
            c.execute('ALTER TABLE session_turn_usage ADD COLUMN future_metadata TEXT')
        db.queue_token_counts('s', turn_id='t', input_tokens=3)
        assert db.get_turn_usage('s', 't')['input_tokens'] == 3
        assert db.get_session('s')['input_tokens'] == 3
    finally:
        db.close()
