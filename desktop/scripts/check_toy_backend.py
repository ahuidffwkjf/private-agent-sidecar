"""Run the frozen four-user backend without Python/Node on PATH or source cwd."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def check(executable):
    with tempfile.TemporaryDirectory(prefix="sidecar-frozen-toy-") as directory:
        root = Path(directory)
        token = secrets.token_hex(32)
        env = {k:v for k,v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"}}
        env["PATH"] = ""
        with (root / "stderr.log").open("w") as log:
            process = subprocess.Popen([str(executable.resolve())], cwd=root, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                process.stdin.write(json.dumps({"token":token,"data_dir":str(root / "workspace"),"mode":"toy"}) + "\n")
                process.stdin.flush()
                with ThreadPoolExecutor(1) as pool:
                    try:
                        line = pool.submit(process.stdout.readline).result(timeout=60)
                    except Exception:
                        process.kill()
                        raise
                if not line.strip():
                    raise AssertionError('Frozen toy backend exited before readiness: ' + (root / 'stderr.log').read_text(errors='replace').replace(token, '[redacted]'))
                ready = json.loads(line)
                assert ready["mode"] == "toy" and len(ready["users"]) == 4
                assert token not in line
                for user in ready["users"]:
                    try:
                        urlopen(user["url"] + "/v1/toy/profile", timeout=5)
                        raise AssertionError("unauthenticated profile")
                    except HTTPError as error:
                        assert error.code == 401
                        error.close()
                    headers = {"Cookie":"sidecar_desktop_session="+token}
                    with urlopen(Request(user["url"] + "/v1/toy/profile",headers=headers),timeout=10) as response:
                        profile = json.load(response)
                    assert profile["mode"] == "toy" and not profile["research"]["consent_active"]
                    with urlopen(Request(user["url"] + "/ui/toy.js",headers=headers),timeout=10) as response:
                        assert response.status == 200 and response.read()
                process.stdin.close()
                assert process.wait(timeout=20) == 0
                for user in ready["users"]:
                    try:
                        urlopen(user["url"] + "/healthz",timeout=2)
                        raise AssertionError("listener survived shutdown")
                    except URLError:
                        pass
                assert token not in (root / "stderr.log").read_text()
                print(json.dumps({"status":"passed","users":4,"empty_path":True,"no_source_cwd":True,
                                  "auth":True,"explicit_consent":True,"shutdown":True}))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    check(Path(sys.argv[1]))
