#!/usr/bin/env python3
"""User-local Sidecar installation, enrollment and supervised startup."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import venv
import webbrowser
from urllib.request import ProxyHandler, Request, build_opener, urlopen

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'sidecar-install/1'
DOWNLOADS = {
    'x86_64': ('amd64', '23c0b1887a5ec422f0d18a9c52b4f5357815febdaae738a1eb54036d10bd9ee6'),
    'aarch64': ('arm64', 'bbb1ab50f24f00effe1e1fd86d0501803fb80793a90785a2a16ff3428f03d8ef'),
    'armv7l': ('armv7', 'cad3994b1f336b67e8a3a5273a9cecb44331d14d57eb32bfe7d0919adeab22b7'),
}


class SetupError(Exception):
    pass


def json_read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_private(path, data):
    path = Path(path)
    if path.is_symlink():
        raise SetupError('Refusing a symlink destination')
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data if isinstance(data, bytes) else data.encode('utf-8'))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    write_private(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise SetupError('Refusing a symlink directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise SetupError('Install directories must be user-owned and mode 0700')
    return path


@contextlib.contextmanager
def home_lock(home):
    if home.exists() and not (home / 'owner.json').exists():
        if any(home.iterdir()):
            raise SetupError('Install home is not managed; select a new empty directory')
    private_dir(home)
    marker = home / 'owner.json'
    if marker.exists():
        if json_read(marker).get('schema') != SCHEMA:
            raise SetupError('Unrecognized install home')
    else:
        write_json(marker, {'schema': SCHEMA})
    lock = home / 'setup.lock'
    if lock.is_symlink():
        raise SetupError('Invalid lock file')
    with lock.open('a') as stream:
        os.chmod(lock, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('Another installer or open session is running') from None
        yield


def run(command, env=None, timeout=300):
    result = subprocess.run(command, env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        # Never print third-party output that may contain credentials/addresses.
        raise SetupError('Subprocess failed; inspect dependency/network configuration locally')
    return result.stdout.decode('utf-8').strip()


def version(binary):
    return run([str(binary), 'version'], timeout=10) == 'v0.7.0'


def source_files(root):
    """Explicit distribution allowlist; never include runtime, plugins or .git."""
    files = [root / name for name in ('pyproject.toml', 'setup.py', 'README.md')]
    files += sorted((root / 'agent_sidecar').glob('*.py'))
    files += sorted((root / 'agent_sidecar/federated').glob('*.py'))
    files += [root / 'scripts' / name for name in (
        'sidecar_setup.py', 'tailcat_deploy.py', 'export_agent_skills.py',
        'package_sidecar_installer.py', 'issue_sidecar_access.py', 'tailcat-serve-relay.sh', 'tailcat-forward-relay.sh')]
    files += [root / 'skills/sidecar-onboarding' / name for name in
              ('SKILL.md', 'references/install.md')]
    files += sorted((root / 'docs').glob('*.md'))
    files += sorted((root / 'docs/interfaces').rglob('*.md'))
    files += sorted((root / 'configs').rglob('*.json'))
    files += [root / 'deploy/tailcat' / name for name in (
        'client.env.example', 'server.env.example', 'relay.env.example',
        'agent-relay.service', 'tailcat-relay.service', 'tailcat-forward.service')]
    required = [root / name for name in ('agent_sidecar/cli.py', 'agent_sidecar/service.py',
                'agent_sidecar/wechat_skills.py', 'docs/AGENT_INSTALL.md', 'docs/TAILCAT_DEPLOYMENT.md')]
    for path in files + required:
        if not path.is_file() or path.is_symlink():
            raise SetupError('Source distribution is incomplete or contains symlinks')
    result = []
    for path in files:
        if path.is_symlink():
            raise SetupError('Source distribution cannot contain symlinks')
        if path.is_file():
            if path.suffix == '.env':
                continue
            result.append(path)
    return sorted(set(result))


def digest_source(root):
    h = hashlib.sha256()
    for path in source_files(root):
        h.update(str(path.relative_to(root)).encode())
        h.update(b'\0')
        h.update(path.read_bytes())
    return h.hexdigest()


def download_tailcat(destination):
    if platform.system() != 'Linux' or platform.machine() not in DOWNLOADS:
        raise SetupError('Automatic download supports Linux amd64/arm64/armv7. On macOS supply installed Tailcat v0.7.0 with --tailcat')
    arch, digest = DOWNLOADS[platform.machine()]
    url = f'https://github.com/tailscale/tailcat/releases/download/v0.7.0/tailcat_0.7.0_linux_{arch}.tar.gz'
    with urlopen(url, timeout=60) as response:
        data = response.read(150 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != digest:
        raise SetupError('Tailcat download SHA-256 mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if Path(m.name).name == 'tailcat' and m.isfile()]
        if len(members) != 1:
            raise SetupError('Unexpected Tailcat archive layout')
        write_private(destination, archive.extractfile(members[0]).read())
    destination.chmod(0o700)


def install(home, binary=None, download=False):
    if sys.version_info < (3, 10) or platform.system() not in ('Darwin', 'Linux'):
        raise SetupError('Installer requires Python 3.10+ on macOS or Linux')
    current = home / 'installed.json'
    digest = digest_source(ROOT)
    if current.exists():
        state = json_read(current)
        if state['source_sha256'] != digest:
            raise SetupError('Different version already installed; use a separate --home for upgrade testing')
        if not Path(state['python']).is_file() or not version(state['tailcat']):
            raise SetupError('Installed environment needs repair; preserve data and use a separate --home')
        return {'status': 'configured' if (home / 'access.json').exists() else 'awaiting_approval',
                'reused': True, 'source_sha256': digest}
    releases = private_dir(home / 'releases')
    release = private_dir(releases / (digest[:12] + '-' + uuid.uuid4().hex[:8]))
    source = private_dir(release / 'source')
    for path in source_files(ROOT):
        target = source / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    tailcat = release / 'tailcat'
    candidate = binary or shutil.which('tailcat')
    if candidate:
        if not version(candidate):
            raise SetupError('Tailcat must be v0.7.0; existing installation was not changed')
        shutil.copyfile(candidate, tailcat)
        tailcat.chmod(0o700)
    elif download:
        download_tailcat(tailcat)
    else:
        raise SetupError('Tailcat missing: supply --tailcat or use --download-tailcat on Linux')
    if not version(tailcat):
        raise SetupError('Tailcat binary verification failed')
    venv.EnvBuilder(with_pip=True).create(release / '.venv')
    python = release / '.venv/bin/python'
    run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', str(source)], timeout=600)
    keys = private_dir(home / 'keys')
    key = keys / 'client.private.json'
    if key.is_symlink():
        raise SetupError('Refusing a symlink identity')
    if not key.exists():
        run([str(tailcat), 'genkey', f'--key={key}', '--client'])
    key.chmod(0o600)
    public = run([str(tailcat), f'--key={key}', 'printpub'])
    if not re.fullmatch(r'nodekey:[0-9a-f]{64}', public):
        raise SetupError('Tailcat did not return a valid public nodekey')
    data_dir = private_dir(home / 'node')
    if (data_dir / 'identity.json').is_symlink():
        raise SetupError('Refusing a symlink identity')
    public_identity = json.loads(run([str(python), '-c',
        'import json,sys; from pathlib import Path; from agent_sidecar.crypto import Identity; '
        'print(json.dumps(Identity.load_or_create(Path(sys.argv[1])).public().as_dict()))',
        str(data_dir / 'identity.json')]))
    skill_dir = release / 'agent-skills'
    run([str(python), str(source / 'scripts/export_agent_skills.py'), str(skill_dir)])
    shutil.copytree(source / 'skills/sidecar-onboarding', skill_dir / 'sidecar-onboarding')
    state = {'schema': SCHEMA, 'source_sha256': digest, 'python': str(python),
             'source': str(source), 'tailcat': str(tailcat), 'key': str(key),
             'skills': str(skill_dir), 'agent_id': public_identity['agent_id']}
    write_json(home / 'enrollment.json', {'schema': 'sidecar-enrollment/1',
        'tailcat_client_public_key': public, 'sidecar_identity': public_identity,
        'source_sha256': digest, 'tailcat_version': 'v0.7.0'})
    command = '#!/bin/sh\nexec ' + ' '.join(shlex.quote(s) for s in
        (str(python), str(source / 'scripts/sidecar_setup.py'), '--home', str(home), 'open', '--browser')) + '\n'
    write_private(home / 'Open Sidecar.command', command)
    (home / 'Open Sidecar.command').chmod(0o700)
    write_json(current, state)
    return {'status': 'awaiting_approval', 'enrollment_file': str(home / 'enrollment.json'),
            'open_entry': str(home / 'Open Sidecar.command'), 'skills': 362,
            'source_sha256': digest}


def state(home):
    value = json_read(home / 'installed.json')
    if value.get('schema') != SCHEMA:
        raise SetupError('Invalid install metadata')
    return value


def validate_access(value, enrollment):
    if not isinstance(value, dict) or value.get('schema') != 'sidecar-access/1':
        raise SetupError('Invalid access bundle schema')
    if value.get('tailcat_client_public_key') != enrollment['tailcat_client_public_key']:
        raise SetupError('Access bundle belongs to another device')
    if value.get('source_sha256') != enrollment['source_sha256']:
        raise SetupError('Client/server source contract does not match')
    if not re.fullmatch(r'tc[A-Za-z0-9._:-]{20,}', str(value.get('tailcat_address', ''))):
        raise SetupError('Invalid Tailcat address')
    token = value.get('relay_token', '')
    if not isinstance(token, str) or not token or not token.isascii() or any(c.isspace() for c in token):
        raise SetupError('Invalid Relay token')
    for name, default in (('relay_port', 8787), ('local_port', 18787), ('node_port', 8802)):
        v = value.get(name, default)
        if type(v) is not int or not 1 <= v <= 65535:
            raise SetupError('Invalid port in access bundle')
    if value.get('local_port', 18787) == value.get('node_port', 8802):
        raise SetupError('Node and tunnel ports must differ')
    ca = value.get('relay_ca_pem')
    if not isinstance(ca, str) or not ca.strip():
        raise SetupError('Access bundle must contain a PEM CA certificate')
    ssl.create_default_context(cadata=ca)


def configure(home, bundle, expected):
    state(home)
    if bundle.stat().st_size > 1024 * 1024:
        raise SetupError('Access bundle is too large')
    data = bundle.read_bytes()
    if not re.fullmatch(r'[0-9a-f]{64}', expected) or hashlib.sha256(data).hexdigest() != expected:
        raise SetupError('Access bundle hash mismatch; verify with the administrator')
    value = json.loads(data)
    validate_access(value, json_read(home / 'enrollment.json'))
    write_json(home / 'access.json', value)
    return {'status': 'configured', 'note': 'Run open to verify the live connection'}


def runtime_env(home):
    metadata = state(home)
    access = json_read(home / 'access.json')
    validate_access(access, json_read(home / 'enrollment.json'))
    credentials = private_dir(home / 'credentials')
    for name, content in (('address', access['tailcat_address']), ('token', access['relay_token']), ('ca.pem', access['relay_ca_pem'])):
        write_private(credentials / name, content)
    env = dict(os.environ)
    env.update(TAILCAT_BIN=metadata['tailcat'], TAILCAT_KEY_FILE=metadata['key'],
        TAILCAT_ADDR_FILE=str(credentials / 'address'), RELAY_TOKEN_FILE=str(credentials / 'token'),
        RELAY_CA_FILE=str(credentials / 'ca.pem'), SSL_CERT_FILE=str(credentials / 'ca.pem'),
        TAILCAT_RELAY_PORT=str(access.get('relay_port', 8787)),
        TAILCAT_LOCAL_PORT=str(access.get('local_port', 18787)),
        SIDECAR_PORT=str(access.get('node_port', 8802)), SIDECAR_DATA_DIR=str(home / 'node'),
        NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost', PYTHONDONTWRITEBYTECODE='1')
    return metadata, env


def free_port(port):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))


def local_api(port, endpoint):
    opener = build_opener(ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}{endpoint}', timeout=2) as response:
        return json.loads(response.read())


def wait_ready(check, children, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise SetupError('A managed process exited before readiness')
        try:
            return check()
        except Exception:
            time.sleep(0.4)
    raise SetupError('Readiness timed out; check allowlist, Relay state and TLS SAN')


def open_node(home, browser=False):
    metadata, env = runtime_env(home)
    ports = [int(env['TAILCAT_LOCAL_PORT']), int(env['SIDECAR_PORT'])]
    for port in ports:
        free_port(port)
    command = [metadata['python'], str(Path(metadata['source']) / 'scripts/tailcat_deploy.py')]
    children = []
    previous = {}
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, interrupt)
        def start(mode):
            child = subprocess.Popen(command + [mode], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.append(child)
            return child
        print(json.dumps({'status': 'connecting'}), flush=True)
        start('forward')
        wait_ready(lambda: run(command + ['probe'], env=env, timeout=15), children, timeout=45)
        start('node')
        def identity_check():
            identity = local_api(ports[1], '/v1/identity')
            if identity.get('agent_id') != metadata['agent_id']:
                raise SetupError('Unexpected local node identity')
            return identity
        wait_ready(identity_check, children)
        url = f'http://127.0.0.1:{ports[1]}/'
        write_json(home / 'session.json', {'status': 'connected', 'pid': os.getpid(),
                                         'agent_id': metadata['agent_id'], 'node_port': ports[1]})
        print(json.dumps({'status': 'connected', 'url': url, 'agent_id': metadata['agent_id']}), flush=True)
        if browser:
            webbrowser.open(url)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise SetupError('A managed process stopped; reopen after fixing the cause')
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        write_json(home / 'session.json', {'status': 'stopped'})
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return {'status': 'stopped', 'data_preserved': True}


def install_skills(home, target):
    source = Path(state(home)['skills'])
    if target.is_symlink():
        raise SetupError('Skill root is a symlink; choose an explicit directory')
    folders = sorted(p for p in source.iterdir() if p.is_dir())
    # Preflight every collision before installing any Skill.
    for folder in folders:
        dest = target / folder.name
        if dest.is_symlink():
            raise SetupError('Skill destination is a symlink')
        if dest.exists():
            if not dest.is_dir() or any(p.is_symlink() for p in dest.rglob('*')):
                raise SetupError('Existing Skill has an unsafe layout')
            expected = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            actual = {str(p.relative_to(dest)): p.read_bytes() for p in dest.rglob('*') if p.is_file()}
            if expected != actual:
                raise SetupError('An existing Skill differs; select another destination (nothing overwritten)')
    target.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        dest = target / folder.name
        if not dest.exists():
            shutil.copytree(folder, dest)
    return {'status': 'skills_installed', 'count': len(folders), 'directory': str(target)}


def status(home):
    if not (home / 'installed.json').exists():
        return {'status': 'not_installed'}
    metadata = state(home)
    stage = 'configured' if (home / 'access.json').exists() else 'awaiting_approval'
    session = json_read(home / 'session.json') if (home / 'session.json').exists() else {}
    if session.get('status') == 'connected':
        try:
            os.kill(session['pid'], 0)
            identity = local_api(session['node_port'], '/v1/identity')
            if identity.get('agent_id') == metadata['agent_id']:
                stage = 'local_node_running'
        except (OSError, ValueError, KeyError):
            pass
    return {'status': stage, 'agent_id': metadata['agent_id'], 'source_sha256': metadata['source_sha256'],
            'note': 'Status does not prove Relay reachability; open performs a live HTTPS probe'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home() / '.local/share/private-agent-sidecar')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    install_parser = commands.add_parser('install')
    install_parser.add_argument('--tailcat', type=Path)
    install_parser.add_argument('--download-tailcat', action='store_true')
    commands.add_parser('status')
    config_parser = commands.add_parser('configure')
    config_parser.add_argument('--access', type=Path, required=True)
    config_parser.add_argument('--sha256', required=True)
    open_parser = commands.add_parser('open')
    open_parser.add_argument('--browser', action='store_true')
    skills_parser = commands.add_parser('skills')
    skills_parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        home = args.home.expanduser().absolute()
        if args.command == 'doctor':
            result = {'platform': platform.system(), 'architecture': platform.machine(),
                      'python': platform.python_version(), 'python_supported': sys.version_info >= (3, 10),
                      'tailcat_found': bool(shutil.which('tailcat')), 'required_tailcat': 'v0.7.0'}
        elif args.command == 'status':
            result = status(home)
        else:
            with home_lock(home):
                if args.command == 'install':
                    result = install(home, args.tailcat, args.download_tailcat)
                elif args.command == 'configure':
                    result = configure(home, args.access, args.sha256)
                elif args.command == 'skills':
                    result = install_skills(home, args.target.expanduser().absolute())
                else:
                    result = open_node(home, args.browser)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError, ssl.SSLError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else type(exc).__name__
        print(json.dumps({'status': 'failed', 'reason': message}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    os.umask(0o077)
    raise SystemExit(main())
