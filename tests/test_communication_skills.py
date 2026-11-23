import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

from agent_sidecar.actors import verify_actor
from agent_sidecar.communication_skills import communication_skill_catalog, communication_catalog_payload
from agent_sidecar.crypto import canonical
from agent_sidecar.mcp import handle_rpc, tools_for
from agent_sidecar.protocol import encrypt_message
from agent_sidecar.service import Sidecar
from agent_sidecar.store import LocalStore
from tests import test_direct_routing as direct_fixture


class CommunicationCatalogTests(unittest.TestCase):
    def test_only_native_communication_features_remain(self):
        skills = communication_skill_catalog()
        self.assertEqual(len(skills), 23)
        self.assertEqual(len({s['skill_id'] for s in skills}), 23)
        self.assertEqual({s['category'] for s in skills}, {'节点与信任', '智能体通信', '公开广播', '项目协作'})
        for skill in skills:
            self.assertTrue(skill['skill_id'].startswith('sidecar.'))
            self.assertEqual(skill['execution'], 'native-sidecar')
            self.assertTrue(set(skill['input_schema']['required']) <= set(skill['input_schema']['properties']))
        payload = communication_catalog_payload(['sidecar.actor.list'])
        self.assertEqual(payload['installed_count'], 1)
        self.assertEqual(sum(c['count'] for c in payload['categories']), 23)

    def test_migration_archives_old_builtin_but_preserves_custom_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / 'state.db')
            try:
                old = {'skill_id': 'wechat.pay.payment_code', 'name': 'Old', 'version': '1',
                       'source': 'wechat-product-map-2026-10-16', 'catalog_schema': 'sidecar-wechat-skill-catalog/1'}
                custom = {**old, 'skill_id': 'custom.tool', 'source': 'local-user'}
                store.upsert_skill(old)
                store.upsert_skill(custom)
                self.assertEqual(store.retire_wechat_catalog(), 1)
                self.assertEqual(store.retire_wechat_catalog(), 0)
                self.assertEqual([s['skill_id'] for s in store.skills()], ['custom.tool'])
                self.assertFalse((Path(directory) / 'skills/wechat.pay.payment_code.json').exists())
                self.assertTrue(list((Path(directory) / 'retired-skills').glob('*.json')))
                self.assertTrue((Path(directory) / 'skills/custom.tool.json').exists())
            finally:
                store.close()

    def test_uninstalled_disabled_unknown_and_malformed_tools_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Sidecar.__new__(Sidecar)
            sidecar.store = LocalStore(Path(directory) / 'state.db')
            sidecar.identity = SimpleNamespace(agent_id='agent:test')
            def call(name, args):
                return handle_rpc(sidecar, {'method': 'tools/call', 'params': {'name': 'skill.' + name, 'arguments': args}})['result']
            try:
                self.assertTrue(call('sidecar.actor.list', {})['isError'])
                sidecar.install_wechat_skills()
                names = {tool['name'] for tool in tools_for(sidecar)}
                self.assertTrue(all('skill.' + s['skill_id'] in names for s in communication_skill_catalog()))
                self.assertEqual(call('sidecar.actor.list', {})['content'][0]['json'], {'actors': []})
                for skill in communication_skill_catalog():
                    self.assertTrue(call(skill['skill_id'], {'unexpected': 'argument'})['isError'])
                self.assertTrue(call('sidecar.actor.register', {})['isError'])
                self.assertTrue(call('sidecar.actor.register', {'name': 42})['isError'])
                self.assertTrue(call('sidecar.node.respond', {'agent_id': 'x', 'decision': 'allow-all'})['isError'])
                self.assertTrue(call('wechat.pay.payment_code', {})['isError'])
                manifest = next(s for s in communication_skill_catalog() if s['skill_id'] == 'sidecar.actor.list')
                sidecar.store.upsert_skill({**manifest, 'enabled': False})
                self.assertTrue(call('sidecar.actor.list', {})['isError'])
            finally:
                sidecar.store.close()


class CommunicationIntegrationTests(unittest.TestCase):
    setUp = direct_fixture.DirectRoutingTests.setUp
    tearDown = direct_fixture.DirectRoutingTests.tearDown
    relay_message_count = direct_fixture.DirectRoutingTests.relay_message_count

    def rpc(self, sidecar, slug, **args):
        server = self.alice_server if sidecar is self.alice else self.bob_server
        request = Request(f'http://127.0.0.1:{server.server_port}/mcp',
                          data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                           'params': {'name': 'skill.sidecar.' + slug, 'arguments': args}}).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            value = json.load(response)['result']
        self.assertFalse(value.get('isError'), value)
        self.exercised.add('sidecar.' + slug)
        return value['content'][0]['json']

    def test_every_native_skill_over_http_and_two_node_lifecycle(self):
        self.exercised = set()
        for node in (self.alice, self.bob):
            node.install_wechat_skills()
        call = self.rpc
        a, b = self.alice, self.bob
        for slug in ('node.identity', 'node.capabilities', 'node.discover', 'node.peers', 'node.notifications', 'project.list'):
            call(a, slug)
        a1 = call(a, 'actor.register', name='Planner', role='Plan')['actor']
        a2 = call(a, 'actor.register', name='Worker')['actor']
        b1 = call(b, 'actor.register', name='Reviewer')['actor']
        b2 = call(b, 'actor.register', name='Executor')['actor']
        self.assertEqual(len({p['actor_id'] for p in (a1, a2, b1, b2)}), 4)
        self.assertEqual(len(call(a, 'actor.list')['actors']), 2)
        for actor, node in ((a1, a), (b2, b)):
            verify_actor(actor, node.identity.public())
        call(a, 'node.trust', agent_id=b.identity.agent_id, expected_signing_key=b.identity.public().signing_key)
        call(b, 'node.notifications')
        call(b, 'node.respond', agent_id=a.identity.agent_id, decision='accept')
        call(a, 'node.notifications')
        peers = call(a, 'node.peers')['peers']
        peer = next(p for p in peers if p['agent_id'] == b.identity.agent_id)
        self.assertEqual(peer['relationship_state'], 'mutual')
        self.assertEqual({p['actor_id'] for p in peer['actors']}, {b1['actor_id'], b2['actor_id']})
        before = self.relay_message_count()
        with patch.object(a.relay, 'broadcasts', side_effect=OSError('Relay unavailable')):
            call(a, 'actor.send', actor_id=a1['actor_id'], recipient_node_id=b.identity.agent_id,
                 recipient_actor_id=b2['actor_id'], text='Targeted message')
        self.assertEqual(self.relay_message_count(), before)
        self.assertEqual(call(b, 'actor.inbox', actor_id=b1['actor_id'])['messages'], [])
        inbox = call(b, 'actor.inbox', actor_id=b2['actor_id'])['messages']
        self.assertEqual(inbox[0]['payload']['sender_actor']['actor_id'], a1['actor_id'])
        self.assertEqual(inbox[0]['payload']['text'], 'Targeted message')
        call(a, 'broadcast.publish', text='Public announcement')
        self.assertTrue(call(b, 'broadcast.list')['broadcasts'])
        project = call(a, 'project.create', name='Communication acceptance', project_id='native-demo')
        self.assertEqual(project['project_id'], 'native-demo')
        invitation = call(a, 'project.invite', project_id='native-demo', agent_id=b.identity.agent_id)['invitation']
        call(b, 'project.invitations')
        call(b, 'project.respond', invitation_id=invitation['invitation_id'], decision='accept')
        call(a, 'project.invitations')
        call(a, 'project.board_post', project_id='native-demo', text='Member-only post')
        call(b, 'project.board_sync', project_id='native-demo')
        board = call(b, 'project.board_read', project_id='native-demo')
        self.assertIn('Member-only post', json.dumps(board))
        call(a, 'project.close', project_id='native-demo')
        call(a, 'node.revoke', agent_id=b.identity.agent_id)
        with self.assertRaises(ValueError):
            a.send_actor_message(a1['actor_id'], b.identity.agent_id, b2['actor_id'], 'After revocation')
        self.assertEqual(self.exercised, {s['skill_id'] for s in communication_skill_catalog()})

    def test_actor_registration_survives_offline_and_restart(self):
        with patch.object(self.alice, 'announce_capabilities', side_effect=OSError('offline')):
            result = self.alice.register_actor('Offline worker')
        self.assertEqual(result['announcement_status'], 'pending')
        reopened = LocalStore(self.root / 'alice/state.db')
        try:
            self.assertEqual(reopened.actor(result['actor']['actor_id']), result['actor'])
        finally:
            reopened.close()

    def test_disabled_direct_route_is_not_resurrected_during_relay_outage(self):
        a, b = self.alice, self.bob
        a.trust(b.identity.agent_id)
        b.trust(a.identity.agent_id)
        self.assertTrue(a._direct_info_for('', b.identity.agent_id)['endpoint'])
        b.set_direct_endpoint('')
        self.assertFalse(a._direct_info_for('', b.identity.agent_id)['enabled'])
        self.assertIsNone(a.store.peer_direct_contact(b.identity.agent_id))
        with patch.object(a.relay, 'broadcasts', side_effect=OSError('offline')):
            self.assertFalse(a._direct_info_for('', b.identity.agent_id)['enabled'])

    def test_actor_messages_reject_spoofing_unknown_recipient_and_untrusted_sender(self):
        a, b = self.alice, self.bob
        a1 = a.register_actor('A')['actor']
        b1 = b.register_actor('B')['actor']
        with self.assertRaises(ValueError):
            a.send_actor_message(a1['actor_id'], b.identity.agent_id, b1['actor_id'], 'Before trust')
        a.trust(b.identity.agent_id)
        b.trust(a.identity.agent_id)
        with self.assertRaises(ValueError):
            a.send_actor_message(b1['actor_id'], b.identity.agent_id, b1['actor_id'], 'Foreign sender')
        with self.assertRaises(ValueError):
            a.send_actor_message(a1['actor_id'], b.identity.agent_id, a1['actor_id'], 'Wrong target node')
        def envelope(profile, recipient=b1['actor_id']):
            return encrypt_message(a.identity, b.identity.public(), a.store.next_sequence(b.identity.agent_id),
                                   'actor.message', {'sender_actor': profile, 'recipient_actor_id': recipient, 'text': 'test'})
        forged = deepcopy(a1)
        forged['name'] = 'Forged name'
        for profile, recipient in ((forged, b1['actor_id']), (b1, b1['actor_id']), (a1, 'missing')):
            packet = envelope(profile, recipient)
            with self.assertRaises(ValueError):
                b.receive_direct({'scope': 'private', 'envelope': packet})
            self.assertFalse(b.store.has_received(packet['message_id']))
        packet = envelope(a1)
        b.receive_direct({'scope': 'private', 'envelope': packet})
        self.assertEqual(b.receive_direct({'scope': 'private', 'envelope': packet})['status'], 'duplicate')
        self.assertEqual(len(b.actor_inbox(b1['actor_id'])['messages']), 1)
