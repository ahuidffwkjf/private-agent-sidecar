"""Small signed request contract shared by toy services, not a trust grant."""
import hashlib
import time
import uuid
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import json

from .crypto import PublicIdentity, canonical, verify_signature


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sign_request(identity, operation, data, request_id=None):
    body = {"protocol": "sidecar-toy-request/1", "operation": operation, "data": data,
            "request_id": request_id or uuid.uuid4().hex, "issued_at": int(time.time()),
            "identity": identity.public().as_dict()}
    return dict(body, signature=identity.sign(canonical(body)))


def verify_request(value, operation=None, fresh=True):
    if not isinstance(value, dict) or set(value) != {"protocol", "operation", "data", "request_id", "issued_at", "identity", "signature"}:
        raise ValueError("invalid signed request")
    if value["protocol"] != "sidecar-toy-request/1" or (operation and operation != value["operation"]):
        raise ValueError("request scope mismatch")
    identity = PublicIdentity.from_dict(value["identity"])
    body = {k: v for k, v in value.items() if k != "signature"}
    if not verify_signature(identity.signing_key, value["signature"], canonical(body)):
        raise ValueError("invalid request signature")
    text(value["request_id"], 128)
    if type(value["issued_at"]) is not int or (fresh and abs(time.time() - value["issued_at"]) > 300):
        raise ValueError("expired request")
    if not isinstance(value["data"], dict):
        raise ValueError("request data must be an object")
    return identity


def text(value, maximum=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid text")
    return value.strip()


def safe_url(url):
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("invalid service URL")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("remote services require TLS")
    return url.rstrip("/")


def post(url, body, headers=None):
    request = Request(url, canonical(body), {"Content-Type": "application/json", **(headers or {})})
    with urlopen(request, timeout=10) as response:
        return json.load(response)
