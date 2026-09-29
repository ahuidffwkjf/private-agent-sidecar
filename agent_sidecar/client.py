from __future__ import annotations

import json
import time
from typing import Any, Dict, List
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .crypto import Identity, PublicIdentity
from .protocol import auth_payload


class RelayClient:
    def __init__(self, base_url: str, identity: Identity, access_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self._access_token = access_token

    def _request(self, method: str, path: str, body: Any = None, headers: Dict[str, str] = None) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._access_token:
            request_headers["X-Relay-Token"] = self._access_token
        request_headers.update(headers or {})
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"relay returned HTTP {exc.code}: {detail}") from exc

    def register(self) -> None:
        self._request("POST", "/v1/agents", self.identity.public().as_dict())

    def relay_url(self) -> str:
        return self.base_url

    @property
    def access_token(self) -> str:
        return self._access_token

    def identities(self) -> List[PublicIdentity]:
        response = self._request("GET", "/v1/agents")
        return [PublicIdentity.from_dict(value) for value in response["agents"]]

    def identity_for(self, agent_id: str) -> PublicIdentity:
        value = self._request("GET", "/v1/agents/" + quote(agent_id, safe=""))
        return PublicIdentity.from_dict(value)

    def send(self, envelope: Dict[str, Any]) -> str:
        return self._request("POST", "/v1/messages", envelope)["message_id"]

    def bootstrap(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/bootstrap")

    def publish_broadcast(self, broadcast: Dict[str, Any]) -> str:
        return self._request("POST", "/v1/broadcasts", broadcast)["broadcast_id"]

    def broadcasts(self, limit: int = 100, channel: str = "", since: int = 0) -> List[Dict[str, Any]]:
        query = urlencode({"limit": limit, "channel": channel, "since": since})
        return self._request("GET", "/v1/broadcasts?" + query)["broadcasts"]

    def inbox(self, limit: int = 100) -> List[Dict[str, Any]]:
        timestamp = int(time.time())
        signature = self.identity.sign(auth_payload("inbox", self.identity.agent_id, timestamp))
        query = urlencode({"limit": limit})
        response = self._request(
            "GET",
            "/v1/messages?" + query,
            headers={
                "X-Agent-Id": self.identity.agent_id,
                "X-Timestamp": str(timestamp),
                "X-Signature": signature,
            },
        )
        return response["messages"]

    def acknowledge(self, message_id: str) -> bool:
        timestamp = int(time.time())
        body = {
            "agent_id": self.identity.agent_id,
            "timestamp": timestamp,
            "signature": self.identity.sign(auth_payload("ack", self.identity.agent_id, timestamp, message_id)),
        }
        return bool(
            self._request("POST", "/v1/messages/" + quote(message_id, safe="") + "/ack", body)["acknowledged"]
        )
