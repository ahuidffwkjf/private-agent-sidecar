import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest
import os
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent_sidecar.desktop import COOKIE
from agent_sidecar.toy_desktop import ToyDesktopRuntime


class ToyDesktopTests(unittest.TestCase):
    def test_basic_hardware_does_not_invoke_device_subprocesses(self):
        from agent_sidecar.capabilities import detect_hardware
        detect_hardware.cache_clear()
        try:
            with patch('agent_sidecar.capabilities.subprocess.run') as run:
                value=detect_hardware('basic')
                run.assert_not_called()
            self.assertIsNone(value['gpu']['present'])
            self.assertEqual(value['camera']['source'],'not-probed')
        finally:
            detect_hardware.cache_clear()
    def test_owned_runtime_preserves_bypasses_and_excludes_loopback(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'no_proxy':'internal.example'}):
            runtime=ToyDesktopRuntime(Path(temporary), secrets.token_hex(32))
            try:
                self.assertEqual(set(os.environ['no_proxy'].split(',')),{'internal.example','127.0.0.1','localhost','::1'})
                self.assertEqual(os.environ['NO_PROXY'],os.environ['no_proxy'])
            finally:
                runtime.close()
    def test_four_users_auth_consent_tasks_restart_and_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            token = secrets.token_hex(32)
            runtime = ToyDesktopRuntime(root, token)
            with patch('socket.getfqdn', side_effect=AssertionError('toy must not query reverse DNS')):
                runtime.start()
            def call(index, path, data=None, authenticated=True):
                headers = {'Cookie': COOKIE + '=' + token} if authenticated else {}
                if data is not None:
                    headers['Content-Type'] = 'application/json'
                req = Request(runtime.users[index]['url'] + path, None if data is None else json.dumps(data).encode(), headers)
                with urlopen(req, timeout=15) as response:
                    return json.load(response)
            try:
                self.assertEqual([u['name'] for u in runtime.users], ['Alice', 'Bob', 'Carol', 'Dave'])
                self.assertEqual(len({u['url'] for u in runtime.users}), 4)
                identities = [call(i, '/v1/identity')['agent_id'] for i in range(4)]
                self.assertEqual(len(set(identities)), 4)
                for i in range(4):
                    with self.assertRaises(HTTPError) as error:
                        call(i, '/v1/identity', authenticated=False)
                    self.assertEqual(error.exception.code, 401)
                    error.exception.close()
                    self.assertFalse(call(i, '/v1/toy/profile')['research']['consent_active'])
                for i in [0,1]:
                    call(i, '/v1/toy/profile', {'nickname':runtime.users[i]['name'], 'consent_active':True})
                task = call(0, '/v1/tasks', {'title':'Native app test', 'original':'Write three steps', 'acceptance':'Three steps'})
                call(1, '/v1/tasks/' + task['task_id'] + '/applications', {'terms_hash':task['terms_hash']})
                call(0, '/v1/tasks/' + task['task_id'] + '/actions', {'action':'approve','node_id':identities[1]})
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    detail = call(1, '/v1/tasks/' + task['task_id'])
                    if detail['group_ready']:
                        break
                    time.sleep(.1)
                self.assertTrue(detail['group_ready'])
                urls = [u['url'] for u in runtime.users]
                runtime.close()
                for url in urls:
                    with self.assertRaises(URLError):
                        urlopen(url + '/healthz',timeout=2)
                token = secrets.token_hex(32)
                runtime = ToyDesktopRuntime(root,token)
                runtime.start()
                self.assertEqual([call(i,'/v1/identity')['agent_id'] for i in range(4)],identities)
                self.assertTrue(any(t['task_id']==task['task_id'] for t in call(0,'/v1/tasks')['tasks']))
                self.assertFalse(call(2,'/v1/toy/profile')['research']['consent_active'])
                event=call(1,'/v1/projects/'+task['project_id']+'/board',{'event_type':'post','content':{'text':'after restart'}})
                board=call(0,'/v1/projects/'+task['project_id']+'/board')
                self.assertTrue(any(e['event_id']==event['event']['event_id'] for e in board['events']))
                self.assertEqual(runtime.sidecars[0].store.trusted_ids(),[])
            finally:
                runtime.close()
