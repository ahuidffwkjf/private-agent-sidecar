import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_sidecar.online import OnlineSidecar
from agent_sidecar.toy_desktop import ToyDesktopRuntime


class OnlineTests(unittest.TestCase):
    def test_relay_ciphertext_invite_membership_chat_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('agent_sidecar.toy_desktop.Sidecar', OnlineSidecar):
                runtime = ToyDesktopRuntime(Path(directory), secrets.token_hex(32))
                runtime.start()
            try:
                a, b, c, d = runtime.sidecars
                for i, node in enumerate(runtime.sidecars):
                    node.toy.stop()
                    node.bounties.stop()
                    node.toy.configure_profile('User ' + str(i), True)
                    node.announce_capabilities()

                def cycle():
                    for node in runtime.sidecars:
                        node.toy.sync()
                        node.pump_online()
                        node.retry_accepted_project_invitations()

                self.assertTrue(next(p for p in a.peer_candidates() if p['agent_id'] == b.identity.agent_id)['online'])
                task = a.toy.act('publish', {'title': 'ECS test', 'original': 'Write a result', 'acceptance': 'One result'})
                task_id, project = task['task_id'], task['project_id']
                a.toy.act('invite', {'task_id': task_id, 'node_id': b.identity.agent_id})
                self.assertFalse(b.toy.detail(task_id)['is_member'])
                with self.assertRaises(ValueError):
                    b.toy.act('accept_invite', {'task_id': task_id, 'terms_hash': 'wrong'})
                b.toy.act('accept_invite', {'task_id': task_id, 'terms_hash': task['terms_hash']})
                for _ in range(6):
                    cycle()
                self.assertTrue(b.toy.detail(task_id)['group_ready'])
                self.assertEqual(a.store.trusted_ids(), [])
                self.assertEqual(b.store.trusted_ids(), [])
                event = b.post_project_board_event(project, 'post', {'text': 'encrypted ECS test'})
                for _ in range(3):
                    cycle()
                self.assertTrue(any(e['event_id'] == event['event']['event_id'] for e in a.project_board(project)['events']))
                a.request_peer_trust(b.identity.agent_id)
                b.poll_peer_trust_notifications()
                b.respond_peer_trust(a.identity.agent_id, 'accept')
                a.poll_peer_trust_notifications()
                result = a.send_direct(b.identity.agent_id, 'chat.message', {'text': 'private ECS test'})
                self.assertEqual(result['status'], 'queued')
                from agent_sidecar.protocol import encrypt_message
                with a.store.lock:
                    operation_id = a.store.db.execute('SELECT operation_id FROM online_outbox WHERE message_id=?', (result['message_id'],)).fetchone()[0]
                forged = encrypt_message(c.identity, a.identity.public(), c.store.next_sequence(a.identity.agent_id),
                    'sidecar.online.receipt', {'operation_id': operation_id, 'message_id': result['message_id']})
                c.relay.send(forged)
                a.pump_online()
                with a.store.lock:
                    self.assertEqual(a.store.db.execute('SELECT status FROM online_outbox WHERE operation_id=?', (operation_id,)).fetchone()[0], 'queued')
                recovered = OnlineSidecar(a.store.data_dir, a.relay.relay_url(), a.relay.access_token, hardware_profile='basic')
                runtime.stores.append(recovered.store)
                runtime.sidecars[0] = recovered
                self.assertEqual(recovered.identity.agent_id, a.identity.agent_id)
                a = recovered
                self.assertGreater(a.online_status()['pending_messages'], 0)
                for _ in range(3):
                    cycle()
                self.assertEqual(a.online_status()['pending_messages'], 0)
                with runtime.stores[0].lock:
                    raw = '\n'.join(str(tuple(row)) for row in runtime.stores[0].db.execute('SELECT envelope_json FROM messages'))
                self.assertNotIn('private ECS test', raw)
                self.assertNotIn('encrypted ECS test', raw)
                blocked = a.send_direct(b.identity.agent_id, 'chat.message', {'text': 'must not send after revocation'})
                a.revoke_peer_trust(b.identity.agent_id)
                a.pump_online()
                with a.store.lock:
                    row = a.store.db.execute('SELECT status FROM online_outbox WHERE message_id=?', (blocked['message_id'],)).fetchone()
                self.assertEqual(row['status'], 'cancelled')
                b.poll_peer_trust_notifications()
                with self.assertRaises(ValueError):
                    b.send_direct(a.identity.agent_id, 'chat.message', {'text': 'blocked'})
            finally:
                runtime.close()

    def test_online_mode_is_explicit_not_a_direct_sidecar_fallback(self):
        from agent_sidecar.service import Sidecar
        self.assertNotEqual(Sidecar._direct_info_for, OnlineSidecar._direct_info_for)
