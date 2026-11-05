"""Tailcat v0.7.0 deployment entrypoint for this checkout (not legacy 0.4).

Only loopback Relay ports are exposed. Secrets stay in operator-owned files;
Tailcat's raw output is suppressed because it can contain the tunnel address.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def path_setting(env, name):
    value = env.get(name, "")
    if not value or not Path(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return Path(value)


def checked_file(path, label, private=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular file, not a symlink")
    if private and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise ValueError(f"{label} must belong to this user with mode 0600 or 0400")
    return path


def secret(env, name):
    path = checked_file(path_setting(env, name), name, private=True)
    if path.stat().st_size > 16384:
        raise ValueError(f"{name} is too large")
    text = path.read_text("utf-8").strip()
    if not text or any(c.isspace() for c in text) or not text.isascii():
        raise ValueError(f"{name} must contain one nonempty ASCII value")
    return text


def port(env, name, default):
    text = env.get(name, str(default))
    if not text.isdecimal() or not 1 <= int(text) <= 65535:
        raise ValueError(f"{name} must be a port from 1 to 65535")
    return int(text)


def tailcat_command(mode, env):
    binary = env.get("TAILCAT_BIN", "tailcat")
    version = subprocess.run([binary, "version"], capture_output=True, text=True,
                             timeout=10, check=False)
    if version.returncode or version.stdout.strip() != "v0.7.0":
        raise ValueError("this deployment is verified against Tailcat v0.7.0 only")
    key = checked_file(path_setting(env, "TAILCAT_KEY_FILE"), "TAILCAT_KEY_FILE", private=True)
    remote = port(env, "TAILCAT_RELAY_PORT", 8787)
    if mode == "serve":
        clients = env.get("TAILCAT_CLIENT_KEYS", "")
        if not re.fullmatch(r"nodekey:[0-9a-f]{64}(,nodekey:[0-9a-f]{64})*", clients):
            raise ValueError("TAILCAT_CLIENT_KEYS must be a nonempty comma-separated nodekey allowlist")
        address_file = path_setting(env, "TAILCAT_ADDR_FILE")
        parent = address_file.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("TAILCAT_ADDR_FILE parent must belong to this user with mode 0700")
        if address_file.exists() or address_file.is_symlink():
            checked_file(address_file, "TAILCAT_ADDR_FILE", private=True)
        return [binary, "serve", f"--key={key}", f"--allow={clients}", str(remote)]
    address = secret(env, "TAILCAT_ADDR_FILE")
    if not re.fullmatch(r"tc[A-Za-z0-9._:-]{20,}", address):
        raise ValueError("TAILCAT_ADDR_FILE does not contain a Tailcat address")
    local = port(env, "TAILCAT_LOCAL_PORT", 18787)
    return [binary, "forward", f"--key={key}", "--bind=127.0.0.1", address, f"{local}:{remote}"]


def relay_config(env):
    cert = checked_file(path_setting(env, "RELAY_TLS_CERT"), "RELAY_TLS_CERT")
    key = checked_file(path_setting(env, "RELAY_TLS_KEY"), "RELAY_TLS_KEY", private=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key, password=lambda: "")
    token = secret(env, "RELAY_TOKEN_FILE")
    database = path_setting(env, "RELAY_DATABASE")
    if not database.parent.is_dir():
        raise ValueError("RELAY_DATABASE parent must already exist")
    limit = env.get("RELAY_RATE_LIMIT", "120")
    if not limit.isdecimal() or int(limit) < 1:
        raise ValueError("RELAY_RATE_LIMIT must be a positive integer")
    return dict(host="127.0.0.1", port=port(env, "TAILCAT_RELAY_PORT", 8787),
                database=database, tls_cert=cert, tls_key=key, access_token=token,
                rate_limit_per_minute=int(limit))


def probe(env):
    ca = checked_file(path_setting(env, "RELAY_CA_FILE"), "RELAY_CA_FILE")
    context = ssl.create_default_context(cafile=str(ca))
    token = secret(env, "RELAY_TOKEN_FILE")
    base = f"https://127.0.0.1:{port(env, 'TAILCAT_LOCAL_PORT', 18787)}"
    for endpoint in ("/healthz", "/v1/agents"):
        request = Request(base + endpoint, headers={"X-Relay-Token": token})
        with urlopen(request, context=context, timeout=10) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            raise ValueError("Relay returned an invalid API response")
        if endpoint == "/healthz" and payload.get("status") != "ok":
            raise ValueError("Relay health check failed")
        if endpoint == "/v1/agents" and not isinstance(payload.get("agents"), list):
            raise ValueError("Relay API does not match this checkout")
    return {"status": "ok", "https_verified": True, "authenticated_directory": True}


def node_config(env):
    ca = checked_file(path_setting(env, "RELAY_CA_FILE"), "RELAY_CA_FILE")
    ssl.create_default_context(cafile=str(ca))
    return dict(host="127.0.0.1", port=port(env, "SIDECAR_PORT", 8802),
                data_dir=path_setting(env, "SIDECAR_DATA_DIR"),
                relay_url=f"https://127.0.0.1:{port(env, 'TAILCAT_LOCAL_PORT', 18787)}",
                relay_token=secret(env, "RELAY_TOKEN_FILE"))


def run_tailcat(command, env, mode):
    child_env = dict(env)
    if mode == "forward":
        # This variable is an output filename only in server mode.
        child_env.pop("TAILCAT_ADDR_FILE", None)
    old_mask = os.umask(0o077)
    try:
        child = subprocess.Popen(command, env=child_env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
    finally:
        os.umask(old_mask)
    previous = {}

    def stop(signum, frame):
        child.terminate()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, stop)
        code = child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if code:
        print("Tailcat stopped; check port conflicts, key/allowlist and network. Raw output is suppressed.", file=sys.stderr)
    return code if code >= 0 else 128 - code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("relay", "serve", "forward", "node", "probe"))
    parser.add_argument("--check", action="store_true", help="validate configuration only; do not start listeners")
    args = parser.parse_args(argv)
    try:
        if args.mode == "probe":
            if args.check:
                raise ValueError("probe performs read-only HTTPS requests; omit --check")
            print(json.dumps(probe(os.environ)))
            return 0
        config = (relay_config(os.environ) if args.mode == "relay" else
                  node_config(os.environ) if args.mode == "node" else None)
        command = tailcat_command(args.mode, os.environ) if config is None else None
        print(json.dumps({"mode": args.mode, "configuration": "valid", "check_only": args.check,
                          "relay_port": port(os.environ, "TAILCAT_RELAY_PORT", 8787),
                          "local_forward_port": port(os.environ, "TAILCAT_LOCAL_PORT", 18787)}), flush=True)
        if args.check:
            return 0
        if config is not None:
            if args.mode == "node":
                # urllib's default TLS context consumes this CA file. Do not
                # weaken verification or put the Relay token in process argv.
                os.environ["SSL_CERT_FILE"] = str(path_setting(os.environ, "RELAY_CA_FILE"))
                from agent_sidecar.service import serve_sidecar
                serve_sidecar(**config)
                return 0
            from agent_sidecar.relay import serve_relay
            serve_relay(**config)
            return 0
        return run_tailcat(command, os.environ, args.mode)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Exception text from external processes/URLs can contain credentials.
        safe = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"Deployment failed: {safe}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
