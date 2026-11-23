"""Explicit administrator export/deletion; credentials are read only from file."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_sidecar.toy_protocol import post, safe_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--admin-token-file", type=Path, required=True)
    parser.add_argument("--node-id")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    delete = commands.add_parser("delete")
    delete.add_argument("--confirm-delete", action="store_true")
    args = parser.parse_args()
    base = safe_url(args.url)
    headers = {"X-Research-Admin": args.admin_token_file.read_text().strip()}
    if args.command == "delete":
        if not args.confirm_delete:
            parser.error("deletion needs --confirm-delete; omit --node-id only to delete the entire experiment")
        result = post(base + "/v1/research/delete", {"node_id": args.node_id}, headers)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        count, cursor = 0, 0
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            while True:
                page = post(base + "/v1/research/query", {"node_id": args.node_id, "after": cursor}, headers)
                if not page["events"]:
                    break
                for event in page["events"]:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                count += len(page["events"])
                cursor = page["next_cursor"]
        result = {"events": count, "output": str(args.output)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
