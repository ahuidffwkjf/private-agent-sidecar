"""Smoke-test the packaged backend on the current OS; never requires source imports."""
import json
import secrets
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def check(executable):
    token = secrets.token_hex(32)
    with tempfile.TemporaryDirectory() as temporary:
        log = Path(temporary) / 'backend.log'
        with log.open('w') as stderr:
            process = subprocess.Popen([str(executable.resolve())], cwd=temporary, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True)
            try:
                process.stdin.write(json.dumps({'token': token, 'data_dir': str(Path(temporary) / 'data')}) + '\n')
                process.stdin.flush()
                pool = ThreadPoolExecutor(1)
                try:
                    ready = pool.submit(process.stdout.readline).result(timeout=60)
                except Exception:
                    process.kill()
                    raise
                finally:
                    pool.shutdown(wait=True)
                assert token not in ready
                url = json.loads(ready)['url']
                try:
                    urlopen(url + '/ui', timeout=5)
                    raise AssertionError('Unauthenticated UI unexpectedly accessible')
                except HTTPError as exc:
                    assert exc.code == 401
                headers = {'Cookie': 'sidecar_desktop_session=' + token}
                skill_count = 0
                for path in ['/ui', '/ui/app.js', '/ui/app.css', '/ui/lucide.js', '/v1/identity', '/v1/skills/catalog']:
                    with urlopen(Request(url + path, headers=headers), timeout=10) as response:
                        assert response.status == 200
                        body = response.read()
                        assert body
                        if path.endswith('/catalog'):
                            catalog = json.loads(body)
                            skill_count = catalog['count']
                            assert skill_count == 23
                            assert catalog['schema'] == 'sidecar-communication-skill-catalog/1'
                            assert skill_count == len(catalog['skills'])
                            assert len({s['skill_id'] for s in catalog['skills']}) == skill_count
                            assert all(s['skill_id'].startswith('sidecar.') and s['execution'] == 'native-sidecar' for s in catalog['skills'])
                process.stdin.close()
                assert process.wait(timeout=15) == 0
                assert token not in log.read_text()
                print(json.dumps({'packaged_backend': str(executable), 'status': 'passed', 'skills': skill_count, 'auth': True, 'shutdown': True}))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    check(Path(sys.argv[1]))
