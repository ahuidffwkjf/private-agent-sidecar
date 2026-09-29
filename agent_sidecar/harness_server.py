from __future__ import annotations

import argparse
from dataclasses import asdict
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .federated.simulator import _dataset
from .federated.types import EvaluateRequest, TrainRequest
from .harness import ToyHarness
from .http_utils import JsonHandler


def make_handler(adapter: Any):
    class HarnessHandler(JsonHandler):
        def do_GET(self) -> None:
            if self.path == "/v1/harness/describe":
                self.send_json(200, asdict(adapter.describe()))
                return
            if self.path == "/healthz":
                self.send_json(200, {"status": "ok", "harness": adapter.name})
                return
            self.send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                body = self.read_json()
                if self.path == "/v1/harness/train":
                    request = TrainRequest(**body["request"])
                    result = adapter.train(request)
                    self.send_json(200, asdict(result))
                    return
                if self.path == "/v1/harness/evaluate":
                    request = EvaluateRequest(**body["request"])
                    result = adapter.evaluate(request)
                    self.send_json(200, asdict(result))
                    return
                if self.path == "/v1/harness/import-global":
                    self.send_json(200, {"imported": True})
                    return
                self.send_json(404, {"error": "not found"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

    return HarnessHandler


def serve_harness(host: str, port: int, seed: int = 7) -> None:
    adapter = ToyHarness(_dataset(seed, 40, 0, False))
    server = ThreadingHTTPServer((host, port), make_handler(adapter))
    print(f"Harness server {adapter.name} listening on http://{host}:{port}")
    server.serve_forever()
