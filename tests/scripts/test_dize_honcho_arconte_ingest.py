"""Honcho acknowledgement invariants with the real SDK envelope and temp SQLite."""
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
        return ingest.apply_candidates(self.policy, candidates or [self.candidate])

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


if __name__ == '__main__':
    unittest.main()
