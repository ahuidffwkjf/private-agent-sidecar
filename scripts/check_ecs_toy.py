"""Exercise real ECS with isolated synthetic users; reports never contain credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import uuid
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler
from urllib.error import HTTPError
import ssl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_sidecar.online_desktop import OnlineDesktopRuntime


def wait(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.5)
    raise AssertionError('ECS condition timed out')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--access-file', required=True)
    parser.add_argument('--tunnel-url')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    access = json.loads(Path(args.access_file).read_text())
    real_url = access['relay_url']
    if args.tunnel_url:
        if not args.tunnel_url.startswith('https://127.0.0.1:'):
            raise ValueError('test tunnel must be local HTTPS')
        access['relay_url'] = args.tunnel_url
    access['experiment_id'] = 'ecs-synthetic-' + uuid.uuid4().hex[:12]
    context = ssl.create_default_context(cadata=access['ca_pem'])
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    started = time.monotonic()
    checks = []
    with opener.open(access['relay_url'] + '/healthz', timeout=10) as response:
        assert json.load(response)['status'] == 'ok'
    try:
        opener.open(access['relay_url'] + '/v1/agents', timeout=10)
        raise AssertionError('unauthenticated directory accepted')
    except HTTPError as error:
        assert error.code == 401
        error.close()
    checks += ['tls-chain-and-hostname-verified', 'unauthenticated-directory-rejected']
    runtime_a = runtime_b = None
    with tempfile.TemporaryDirectory(prefix='ecs-real-acceptance-') as directory:
        root = Path(directory)
        access_file = root / 'access.private.json'
        access_file.write_text(json.dumps(access))
        access_file.chmod(0o600)
        try:
            runtime_a = OnlineDesktopRuntime(root / 'a', secrets.token_hex(32), access_file)
            runtime_b = OnlineDesktopRuntime(root / 'b', secrets.token_hex(32), access_file)
            runtime_a.start()
            runtime_b.start()
            a, b = runtime_a.sidecar, runtime_b.sidecar
            a.toy.configure_profile('ECS synthetic acceptance A', True)
            b.toy.configure_profile('ECS synthetic acceptance B', True)
            a.announce_capabilities()
            b.announce_capabilities()
            assert any(p['agent_id'] == b.identity.agent_id and p['online'] for p in a.peer_candidates())
            checks.append('signed-online-discovery')
            task = a.toy.act('publish', {'title': 'Isolated ECS acceptance', 'original': 'Synthetic task; no real user data', 'acceptance': 'Explicit invitation and encrypted group message'})
            a.toy.act('invite', {'task_id': task['task_id'], 'node_id': b.identity.agent_id})
            assert not b.toy.detail(task['task_id'])['is_member']
            b.toy.act('accept_invite', {'task_id': task['task_id'], 'terms_hash': task['terms_hash']})
            join_started = time.monotonic()
            wait(lambda: b.toy.detail(task['task_id'])['group_ready'])
            join_ms = round((time.monotonic() - join_started) * 1000)
            checks += ['directed-task-invitation', 'explicit-acceptance-before-membership', 'membership-delivery-confirmed']
            event = b.post_project_board_event(task['project_id'], 'post', {'text': 'Synthetic encrypted ECS group result'})
            wait(lambda: any(e['event_id'] == event['event']['event_id'] for e in a.project_board(task['project_id'])['events']))
            checks.append('encrypted-group-delivery-through-ecs')
            a.request_peer_trust(b.identity.agent_id)
            wait(lambda: bool(b.store.latest_peer_trust_statement(a.identity.agent_id, 'incoming')))
            b.respond_peer_trust(a.identity.agent_id, 'accept')
            wait(lambda: a._private_trust_ready(b.identity.agent_id))
            message = a.send_direct(b.identity.agent_id, 'chat.message', {'text': 'Synthetic private ECS message'})
            wait(lambda: b.store.has_received(message['message_id']))
            checks.append('mutual-trust-private-message')
            identity = b.identity.agent_id
            runtime_b.close()
            runtime_b = OnlineDesktopRuntime(root / 'b', secrets.token_hex(32), access_file)
            runtime_b.start()
            b = runtime_b.sidecar
            assert b.identity.agent_id == identity
            assert b.toy.detail(task['task_id'])['group_ready']
            checks.append('restart-restores-identity-and-membership')
            a.toy.act('cancel', {'task_id': task['task_id']})
            a.toy.configure_profile('ECS synthetic acceptance A', False)
            b.toy.configure_profile('ECS synthetic acceptance B', False)
            result = {'status': 'passed', 'relay_url': real_url, 'public_ingress_verified': not bool(args.tunnel_url),
                'path': 'SSH tunnel to actual ECS' if args.tunnel_url else 'direct public HTTPS',
                'experiment_id': access['experiment_id'], 'users': 2, 'checks': checks,
                'join_ms': join_ms, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                'summary': 'synthetic data; not real-model acceptance; original OrangePi history not migrated'}
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result, indent=2))
        finally:
            if runtime_a:
                runtime_a.close()
            if runtime_b:
                runtime_b.close()


if __name__ == '__main__':
    main()
