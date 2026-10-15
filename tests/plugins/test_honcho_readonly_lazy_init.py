import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock
from plugins.memory.honcho import HonchoMemoryProvider
class GuardTests(unittest.TestCase):
 def provider(self):
  p=HonchoMemoryProvider();p._config=N(read_only_injection=True,session_peer_aliases={'bound':'peer'},context_tokens=2000);p._session_key='bound';p._manager=N(get_session_context=Mock(return_value={'representation':'Verified fact'}));p._ensure_session=Mock(return_value=True);p._is_trivial_prompt=Mock(return_value=False);return p
 def test_write_tools_refused(self):
  p=self.provider();p._read_only=True
  self.assertIn('disabled',p.handle_tool_call('honcho_conclude',{'conclusion':'test'}).lower())
  p._ensure_session.assert_not_called()
 def test_card_write_refused(self):
  p=self.provider();p._read_only=True
  self.assertIn('disabled',p.handle_tool_call('honcho_profile',{'card':'test'}).lower())
  p._ensure_session.assert_not_called()
 def test_reasoning_refused(self):
  p=self.provider();p._read_only=True
  self.assertIn('disabled',p.handle_tool_call('honcho_reasoning',{'query':'test'}).lower())
  p._ensure_session.assert_not_called()
 def test_lazy_bound(self):
  p=self.provider();self.assertIn('Verified fact',p._prefetch_read_only('question'));p._ensure_session.assert_called_once();self.assertNotIn('relevant_only',p._manager.get_session_context.call_args.kwargs)
 def test_unbound(self):
  p=self.provider();p._session_key='unbound';self.assertEqual(p._prefetch_read_only('question'),'');p._ensure_session.assert_not_called()
 def test_disabled(self):
  p=self.provider();p._config.read_only_injection=False;self.assertEqual(p._prefetch_read_only('question'),'');p._ensure_session.assert_not_called()
 def test_cron(self):
  p=self.provider();p._cron_skipped=True;self.assertEqual(p._prefetch_read_only('question'),'');p._ensure_session.assert_not_called()
 def test_init_failure(self):
  p=self.provider();p._ensure_session.return_value=False;self.assertEqual(p._prefetch_read_only('question'),'');p._manager.get_session_context.assert_not_called()
 def test_empty_recall(self):
  p=self.provider();p._manager.get_session_context.return_value={};self.assertEqual(p._prefetch_read_only('question'),'')
 def test_lookup_failure(self):
  p=self.provider();p._manager.get_session_context.side_effect=TimeoutError();self.assertEqual(p._prefetch_read_only('question'),'')
if __name__=='__main__':unittest.main()
