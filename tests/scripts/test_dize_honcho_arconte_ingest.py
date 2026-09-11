"""Honcho acknowledgement invariants with the real SDK envelope and temp SQLite."""
from dataclasses import replace
import json
import datetime as dt
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from honcho.session import Message, MessageResponse
from honcho.pagination import SyncPage

SOURCE = Path(__file__).parents[2] / 'scripts/dize_honcho_arconte_ingest.py'
spec = importlib.util.spec_from_file_location('ingestor_under_test', SOURCE)
ingest = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ingest
spec.loader.exec_module(ingest)


class AcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name) / 'ledger.sqlite'
        self.policy = dict(client_env='/synthetic-never-read', honcho_base_url='http://synthetic.invalid',
                           workspace='synthetic', max_queue_backlog=10,
                           assistant_peer='assistant', ledger_db=str(self.ledger))
        self.candidate = ingest.Candidate(1, 'telegram', 'dm', 'user', 'session', 'session',
            'person', 'ref', 'correct synthetic content', None, {'source_ref': 'ref', 'source': 'telegram'})
        self.existing = []
        self.created = [self.message()]
        self.posts = 0
        owner = self

        class Page(SimpleNamespace):
            def __iter__(self): return iter(self.items)

        class Session:
            def messages(self, **kwargs):
                owner.lookup_size = kwargs['size']
                return Page(items=owner.existing)
            def add_messages(self, messages):
                owner.posts += 1
                return owner.created

        class Client:
            def __init__(self, **kwargs): pass
            def set_configuration(self, configuration): pass
            def queue_status(self):
                return SimpleNamespace(pending_work_units=0, in_progress_work_units=0)
            def peer(self, *args, **kwargs): return SimpleNamespace(id='person')
            def session(self, *args, **kwargs): return Session()

        self.addCleanup(patch.stopall)
        patch('honcho.Honcho', Client).start()
        patch.object(ingest, 'load_env', return_value={'HONCHO_API_KEY': 'synthetic-test-value'}).start()

    def message(self, candidate=None, **changes):
        c = candidate or self.candidate
        values = dict(id='remote-' + str(c.source_message_id), content=c.content, peer_id=c.peer_id,
                      session_id=c.session_id, workspace_id='synthetic', metadata=dict(c.metadata),
                      created_at=dt.datetime.now(dt.timezone.utc), token_count=3)
        values.update(changes)
        return Message(**values)

    def apply(self, candidates=None):
        prepared = [replace(c, confirmation={'projection_contract': ingest.projection_contract(self.policy),
                    'source_version': 'a'*64, 'key_id': 'b'*64, 'gateway_ref': 'd'*64}) for c in (candidates or [self.candidate])]
        return ingest.apply_candidates(self.policy, prepared)

    def rows(self):
        with sqlite3.connect(self.ledger) as con:
            return con.execute('SELECT source_message_id,content_hash FROM ingested_messages').fetchall()

    def test_matching_remote_replay_acknowledged_without_post(self):
        self.existing = [self.message()]
        result = self.apply()
        self.assertEqual(result['deduplicated_remote'], 1)
        self.assertEqual(self.posts, 0)
        self.assertEqual(self.rows(), [(1, hashlib.sha256(self.candidate.content.encode()).hexdigest())])

    def test_correct_new_response_acknowledged(self):
        self.assertEqual(self.apply()['posted'], 1)
        self.assertEqual(len(self.rows()), 1)

    def test_old_remote_content_is_not_acknowledged_as_new(self):
        self.existing = [self.message(content='old synthetic content')]
        with self.assertRaisesRegex(RuntimeError, 'acknowledgement mismatch'):
            self.apply()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.posts, 0)

    def test_mismatched_remote_identity_never_writes_ledger(self):
        for field, value in [('peer_id','other'), ('session_id','other'), ('workspace_id','other'),
                             ('metadata',{}), ('metadata',{'source_ref':'other'}), ('id','')]:
            with self.subTest(field=field, value=value):
                self.existing = [self.message(**{field:value})]
                with self.assertRaises(RuntimeError): self.apply()
                self.assertEqual(self.rows(), [])

    def test_missing_content_is_not_acknowledged(self):
        self.existing = [self.message()]
        del self.existing[0].content
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.rows(), [])

    def test_remote_source_metadata_mismatch_is_rejected(self):
        self.existing = [self.message(metadata={'source_ref':'ref','source':'discord'})]
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.rows(), [])

    def test_duplicate_source_ref_is_ambiguous(self):
        self.existing = [self.message(), self.message(id='other-id')]
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'): self.apply()
        self.assertEqual(self.lookup_size, 2)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.posts, 0)

    def test_new_response_wrong_content_rejected(self):
        self.created = [self.message(content='wrong')]
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.posts, 1)
        self.assertEqual(self.rows(), [])

    def test_new_response_wrong_identity_rejected(self):
        self.created = [self.message(peer_id='other')]
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.rows(), [])

    def test_new_response_cardinality_mismatch_rejected(self):
        self.created = []
        with self.assertRaisesRegex(RuntimeError, 'cardinality'): self.apply()
        self.assertEqual(self.rows(), [])

    def test_entire_returned_batch_validated_before_ledger_write(self):
        c2 = ingest.Candidate(2, 'telegram','dm','user','session','session','person','ref2','second',None,
                              {'source_ref':'ref2','source':'telegram'})
        self.created = [self.message(), self.message(c2, content='wrong')]
        with self.assertRaises(RuntimeError): self.apply([self.candidate,c2])
        self.assertEqual(self.rows(), [])

    def test_duplicate_remote_ids_in_new_batch_rejected(self):
        c2 = ingest.Candidate(2, 'telegram','dm','user','session','session','person','ref2','second',None,
                              {'source_ref':'ref2','source':'telegram'})
        self.created = [self.message(), self.message(c2, id='remote-1')]
        with self.assertRaisesRegex(RuntimeError, 'duplicate'): self.apply([self.candidate,c2])
        self.assertEqual(self.rows(), [])

    def test_reordered_response_does_not_acknowledge_wrong_ids(self):
        c2 = ingest.Candidate(2, 'telegram','dm','user','session','session','person','ref2','second',None,
                              {'source_ref':'ref2','source':'telegram'})
        self.created = [self.message(c2), self.message()]
        with self.assertRaises(RuntimeError): self.apply([self.candidate,c2])
        self.assertEqual(self.rows(), [])

    def test_existing_ledger_row_preserved_on_mismatch(self):
        ledger=ingest.ledger_connection(self.ledger)
        ledger.execute('INSERT INTO ingested_messages VALUES (?,?,?,?,?,?)',
                       (1,'telegram','ref','old-id','old-confirmed-hash','synthetic'))
        ledger.commit();ledger.close()
        self.existing = [self.message(content='old content')]
        with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(self.rows(),[(1,'old-confirmed-hash')])

    def test_recovery_after_remote_success_before_ledger_commit(self):
        class FailedLedger:
            def execute(self,*args): raise sqlite3.OperationalError('synthetic failure')
            def close(self): pass
        self.existing = [self.message()]
        with patch.object(ingest,'ledger_connection',return_value=FailedLedger()):
            with self.assertRaises(sqlite3.OperationalError):self.apply()
        self.apply()
        self.assertEqual(self.posts,0)
        self.assertEqual(len(self.rows()),1)

    def test_confirmation_binding_saved_and_replay_stays_single(self):
        self.apply()
        self.existing = [self.message()]
        self.apply()
        with ingest.source_connection(self.ledger) as con:
            rows = con.execute('SELECT * FROM ingested_messages').fetchall()
            evidence = con.execute('SELECT evidence_json FROM message_confirmations').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(evidence), 1)
        binding = json.loads(evidence[0][0])
        self.assertEqual(binding['peer'], self.candidate.peer_id)
        self.assertEqual(binding['session'], self.candidate.session_id)
        self.assertEqual(binding['workspace'], self.policy['workspace'])
        self.assertNotIn(self.candidate.content, evidence[0][0])
        self.assertEqual(ingest.confirmation_state(rows[0], evidence[0][0], ingest.projection_contract(self.policy)), 'RECORDED_CONTRACT_MATCHES')

    def test_failure_between_legacy_and_binding_rolls_back_both(self):
        con = ingest.ledger_connection(self.ledger)
        con.execute("CREATE TRIGGER fail_binding BEFORE INSERT ON message_confirmations BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        con.commit(); con.close()
        with self.assertRaises(sqlite3.IntegrityError): self.apply()
        self.assertEqual(self.rows(), [])
        with sqlite3.connect(self.ledger) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM message_confirmations').fetchone()[0], 0)
            con.execute('DROP TRIGGER fail_binding')
        self.existing = [self.message()]
        self.apply()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.posts, 1)

    def test_legacy_writer_invalidates_binding_and_no_backfill(self):
        self.apply()
        with sqlite3.connect(self.ledger) as con:
            con.execute("UPDATE ingested_messages SET ingested_at='legacy-write'")
        with ingest.source_connection(self.ledger) as con:
            row = con.execute('SELECT * FROM ingested_messages').fetchone()
            evidence = con.execute('SELECT evidence_json FROM message_confirmations').fetchone()[0]
        self.assertEqual(ingest.confirmation_state(row, evidence, ingest.projection_contract(self.policy)), 'BINDING_UNVERIFIED')
        self.assertEqual(ingest.confirmation_state(row, None, ingest.projection_contract(self.policy)), 'LEGACY_UNVERIFIED')

    def test_policy_change_without_content_change_detected(self):
        self.apply()
        with ingest.source_connection(self.ledger) as con:
            row = con.execute('SELECT * FROM ingested_messages').fetchone()
            evidence = con.execute('SELECT evidence_json FROM message_confirmations').fetchone()[0]
        changed = {**self.policy, 'redact_secrets': False}
        self.assertEqual(ingest.confirmation_state(row, evidence, ingest.projection_contract(changed)), 'CONTRACT_CHANGED')
        with patch.object(ingest, 'GOOGLE_OAUTH_CLIENT_ID_RE', __import__('re').compile('changed')):
            self.assertNotEqual(ingest.projection_contract(self.policy), json.loads(evidence)['projection_contract'])

    def test_unversioned_or_stale_candidate_is_not_acknowledged(self):
        for evidence in [None, {'projection_contract': 'c'*64, 'source_version': 'a'*64, 'key_id': 'b'*64, 'gateway_ref': 'd'*64}]:
            with self.subTest(evidence=evidence):
                with self.assertRaisesRegex(RuntimeError, 'projection contract'):
                    ingest.apply_candidates(self.policy, [replace(self.candidate, confirmation=evidence)])
                self.assertFalse(self.ledger.exists())
                self.assertEqual(self.posts, 0)

    def test_real_sdk_page_does_not_fetch_following_pages(self):
        def forbidden_fetch(page):
            raise AssertionError('must not fetch another page')
        messages=[vars(self.message()), vars(self.message(id='another'))]
        page=SyncPage(dict(items=messages,page=1,size=2,total=100,pages=50),
                      MessageResponse, Message.from_api_response, forbidden_fetch)
        session=SimpleNamespace(messages=lambda **kwargs:page)
        with self.assertRaisesRegex(RuntimeError,'ambiguous'):
            ingest.remote_existing(session,'ref')

    def test_error_receipt_does_not_expose_message(self):
        result=ingest.error_receipt(RuntimeError('private synthetic content'))
        self.assertNotIn('private synthetic content',str(result))


class AuditPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'source.sqlite';self.ledger=self.root/'ledger.sqlite'
        import json
        self.json=json
        con=sqlite3.connect(self.db)
        con.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,chat_type TEXT,origin_json TEXT); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,content TEXT,role TEXT,compacted INTEGER,active INTEGER,display_kind TEXT);")
        origin=json.dumps(dict(platform='telegram',chat_type='dm',chat_id='synthetic',user_id='synthetic'))
        con.execute('INSERT INTO sessions VALUES (?,?,?,?)',('s','telegram','dm',origin));con.commit();con.close()
        ingest.ledger_connection(self.ledger).close()
        self.policy=dict(version=1,history_order='newest-first',source_db=str(self.db),ledger_db=str(self.ledger),allowed_sources=['telegram'],denied_sources=['buzz'],allowed_chat_types=['dm'],allowed_roles=['user'],max_messages_per_run=3,max_chars_per_message=1000,include_compacted=False,include_hidden=False)
        self.insert(1)

    def insert(self, key, text='synthetic original'):
        con=sqlite3.connect(self.db);con.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)',(key,'s',text,'user',0,1,'normal'));con.commit();con.close()
        con=sqlite3.connect(self.ledger);con.execute('INSERT INTO ingested_messages VALUES (?,?,?,?,?,?)',(key,'telegram','ref'+str(key),'remote'+str(key),hashlib.sha256(text.encode()).hexdigest(),'synthetic'));con.commit();con.close()

    def edit(self,sql,args=()):
        with sqlite3.connect(self.db) as con:con.execute(sql,args)

    def test_matching_content_never_certifies_identity_or_remote(self):
        before=(self.db.read_bytes(),self.ledger.read_bytes())
        result=ingest.audit_ingested_page(self.policy,3)
        self.assertEqual(result['counts'],{'projected_content_matches':1})
        self.assertEqual(result['identity'],'NOT_CHECKED');self.assertEqual(result['remote'],'NOT_CHECKED')
        self.assertEqual(before,(self.db.read_bytes(),self.ledger.read_bytes()))

    def test_changed_content_requires_review(self):
        self.edit("UPDATE messages SET content='synthetic changed'")
        result=ingest.audit_ingested_page(self.policy,3)
        self.assertEqual(result['status'],'review_required');self.assertEqual(result['counts'],{'projected_content_changed':1})

    def test_missing_source_is_not_retirement(self):
        self.edit('DELETE FROM messages')
        result=ingest.audit_ingested_page(self.policy,3)
        self.assertEqual(result['counts'],{'source_missing_unverified':1})
        self.assertEqual(result['retirement'],'NOT_AUTHORIZED_BY_ABSENCE')

    def test_inactive_hidden_compacted_and_wrong_role_require_review(self):
        for field,value in [('active',0),('display_kind','hidden'),('compacted',1),('role','tool')]:
            with self.subTest(field=field):
                self.edit("UPDATE messages SET active=1,display_kind='normal',compacted=0,role='user'")
                self.edit('UPDATE messages SET '+field+'=?',(value,))
                self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'no_longer_eligible':1})

    def test_orphan_session_requires_review(self):
        self.edit('DELETE FROM sessions')
        self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'source_identity_changed':1})

    def test_invalid_origin_does_not_pass_on_content_match(self):
        self.edit("UPDATE sessions SET origin_json='{}'")
        self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'no_longer_eligible':1})

    def test_source_change_does_not_pass_on_content_match(self):
        self.edit("UPDATE sessions SET source='discord'")
        self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'source_identity_changed':1})

    def test_legacy_bad_hash_stays_unverified(self):
        with sqlite3.connect(self.ledger) as con:con.execute("UPDATE ingested_messages SET content_hash='legacy'")
        self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'ledger_hash_unverified':1})

    def test_projection_policy_change_is_detected(self):
        self.policy['max_chars_per_message']=4
        self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'projected_content_changed':1})

    def test_page_cursor_and_fixed_upper_bound(self):
        for i in range(2,8):self.insert(i)
        first=ingest.audit_ingested_page(self.policy,3)
        self.assertEqual((first['scanned'],first['next_after_id'],first['through_id'],first['has_more']),(3,3,7,True))
        self.insert(8)
        second=ingest.audit_ingested_page(self.policy,3,first['next_after_id'],first['through_id'])
        third=ingest.audit_ingested_page(self.policy,3,second['next_after_id'],second['through_id'])
        self.assertEqual((second['scanned'],third['scanned'],third['next_after_id'],third['has_more']),(3,1,7,False))

    def test_invalid_cursors_or_size_refused_before_reads(self):
        with patch.object(ingest,'source_connection',side_effect=AssertionError('must not open')):
            for args in [(0,0,None),(4,0,None),(1,-1,None),(1,2,1)]:
                with self.assertRaises(ValueError):ingest.audit_ingested_page(self.policy,*args)

    def test_missing_database_does_not_create_it(self):
        self.policy['source_db']=str(self.root/'missing.sqlite')
        with self.assertRaises(sqlite3.OperationalError):ingest.audit_ingested_page(self.policy,3)
        self.assertFalse(Path(self.policy['source_db']).exists())

    def test_audit_does_not_call_ingestion_or_client_environment(self):
        with patch.object(ingest,'ledger_connection',side_effect=AssertionError('no writes')),patch.object(ingest,'load_env',side_effect=AssertionError('no credentials')),patch.object(ingest,'ingested_ids',side_effect=AssertionError('no full ledger')):
            self.assertEqual(ingest.audit_ingested_page(self.policy,3)['scanned'],1)

    def test_large_ledger_audit_has_bounded_sql_work(self):
        with sqlite3.connect(self.ledger) as con:
            con.executemany('INSERT INTO ingested_messages VALUES (?,?,?,?,?,?)',[(i,'telegram','ref'+str(i),'remote'+str(i),'a'*64,'synthetic') for i in range(2,5001)])
        real=ingest.source_connection;steps=[]
        def bounded(path):
            con=real(path)
            def progress():
                steps.append(1)
                return int(len(steps)>10)
            con.set_progress_handler(progress,100)
            return con
        with patch.object(ingest,'source_connection',side_effect=bounded):
            result=ingest.audit_ingested_page(self.policy,3)
        self.assertEqual(result['scanned'],3);self.assertTrue(result['has_more'])
        self.assertLessEqual(len(steps),10)

    def test_cli_audit_never_loads_client_environment(self):
        import io
        from contextlib import redirect_stdout
        policy=self.root/'policy.json';policy.write_text(self.json.dumps(self.policy))
        self.edit("UPDATE messages SET content='changed'")
        with patch.object(sys,'argv',['ingest','--policy',str(policy),'--audit-ingested','--limit','2']),patch.object(ingest,'load_env',side_effect=AssertionError('no credentials')),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ingest.main(),2)
        self.assertEqual(self.json.loads(out.getvalue())['status'],'review_required')

    def test_forbidden_scope_never_reads_message_body(self):
        real=ingest.source_connection
        def guarded(path):
            con=real(path)
            con.set_authorizer(lambda action,table,column,*rest: sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_READ and table=='messages' and column=='content'
                else sqlite3.SQLITE_OK)
            return con
        self.edit("UPDATE sessions SET source='discord'")
        with patch.object(ingest,'source_connection',side_effect=guarded):
            self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'source_identity_changed':1})
        self.policy['allowed_sources']=['discord']
        with patch.object(ingest,'source_connection',side_effect=guarded):
            self.assertEqual(ingest.audit_ingested_page(self.policy,3)['counts'],{'policy_scope_excluded':1})

    def test_existing_ingestion_uses_shared_projection(self):
        with sqlite3.connect(self.db) as con:
            for field,kind in [('session_key','TEXT'),('chat_id','TEXT'),('thread_id','TEXT'),('started_at','REAL')]:
                con.execute('ALTER TABLE sessions ADD COLUMN '+field+' '+kind)
            con.execute('ALTER TABLE messages ADD COLUMN timestamp REAL')
        with sqlite3.connect(self.ledger) as con:con.execute('DELETE FROM ingested_messages')
        self.policy['max_chars_per_message']=4;self.policy['assistant_peer']='assistant'
        with patch.object(ingest,'gateway_session_id',return_value='synthetic-session'):
            candidates,counts,rejected=ingest.collect_candidates(self.policy,b'synthetic-key',3)
        self.assertEqual(len(candidates),1);self.assertEqual(candidates[0].content,'synt')
        self.assertTrue(candidates[0].metadata['truncated']);self.assertEqual(dict(rejected),{})
        first = candidates[0]
        self.assertEqual(first.confirmation['projection_contract'], ingest.projection_contract(self.policy))
        self.assertNotIn('synthetic-session', json.dumps(first.confirmation))
        with patch.object(ingest,'gateway_session_id',return_value='synthetic-session'):
            changed, _, _ = ingest.collect_candidates(self.policy,b'other-key',3)
        self.assertNotEqual(first.confirmation['key_id'], changed[0].confirmation['key_id'])
        self.assertNotEqual(first.confirmation['source_version'], changed[0].confirmation['source_version'])
        self.assertNotEqual(first.source_ref, changed[0].source_ref)

    def test_projection_uses_same_redaction_and_truncation(self):
        text='prefix https://synthetic-user:synthetic-password@example.test end'
        content,truncated=ingest.project_content(text,self.policy)
        self.assertNotIn('synthetic-password',content)
        self.policy['max_chars_per_message']=4
        short,clipped=ingest.project_content(text,self.policy)
        self.assertEqual(short,content[:4]);self.assertTrue(clipped)


if __name__ == '__main__':
    unittest.main()
