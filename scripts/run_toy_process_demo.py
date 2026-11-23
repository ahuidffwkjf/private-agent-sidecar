"""Four independent node processes, real HTTP groups and research collection."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toy_workspace import start, stop
from agent_sidecar.toy_protocol import post


def get(url):
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def run(output):
    workspace = start(output, demo=True)
    timings = []
    try:
        users = workspace["users"]
        urls = [u["url"] for u in users]
        identities = [get(url + "/v1/identity") for url in urls]
        for user in users:
            post(user["url"] + "/v1/toy/profile", {"nickname": user["name"], "consent_active": True})

        def wait(check, label):
            begin = time.monotonic()
            deadline = begin + 30
            while time.monotonic() < deadline:
                if check():
                    timings.append({"operation": label, "ms": (time.monotonic() - begin) * 1000})
                    return
                time.sleep(.2)
            raise AssertionError(label)

        def action(index, task_id, name, **data):
            return post(urls[index] + "/v1/tasks/" + task_id + "/actions", dict(data, action=name))

        task = post(urls[0] + "/v1/tasks", {"title": "协作比较两种方案", "original": "分别分析方案的优点和局限，整理带来源的比较表。", "acceptance": "比较表与来源说明"})
        identifier = task["task_id"]
        for index in [1, 2, 3]:
            action(index, identifier, "apply", terms_hash=task["terms_hash"], proposal="愿意参加")
            action(0, identifier, "approve", node_id=identities[index]["agent_id"])
        wait(lambda: all(get(url + "/v1/tasks/" + identifier)["group_ready"] for url in urls), "root-group-join")
        wait(lambda: get(urls[0] + "/v1/tasks/" + identifier)["summary"]["status"] == "ready", "summary")
        post(urls[1] + "/v1/projects/" + task["project_id"] + "/board", {"event_type": "post", "content": {"text": "研究组消息完整采集测试"}})
        # Confirm directional node trust independently from group membership.
        post(urls[0] + "/v1/peers/trust-requests", {"agent_id": identities[1]["agent_id"], "expected_signing_key": identities[1]["signing_key"]})
        get(urls[1] + "/v1/peers/trust-notifications")
        post(urls[1] + "/v1/peers/trust-response", {"agent_id": identities[0]["agent_id"], "decision": "accept"})
        get(urls[0] + "/v1/peers/trust-notifications")
        post(urls[0] + "/v1/messages/send", {"target": {"kind": "private", "recipient_id": identities[1]["agent_id"]}, "message": {"type": "chat.message", "payload": {"text": "实验私聊采集测试"}}})
        child = post(urls[0] + "/v1/tasks/" + identifier + "/subtasks", {"title": "分析方案 A", "original": "分析 A 的优点", "acceptance": "优点清单", "owner_id": identities[1]["agent_id"]})
        action(1, child["task_id"], "accept_subtask", terms_hash=child["terms_hash"])
        action(1, child["task_id"], "invite", node_id=identities[2]["agent_id"])
        action(2, child["task_id"], "accept_invite", terms_hash=child["terms_hash"])
        wait(lambda: get(urls[2] + "/v1/tasks/" + child["task_id"])["group_ready"], "child-group-join")
        grand = post(urls[1] + "/v1/tasks/" + child["task_id"] + "/subtasks", {"title": "核实来源", "original": "核实 A 的参考来源", "acceptance": "来源列表", "owner_id": identities[2]["agent_id"]})
        action(2, grand["task_id"], "accept_subtask", terms_hash=grand["terms_hash"])
        submitted = action(2, grand["task_id"], "submit", text="来源核实完成")
        action(1, grand["task_id"], "review", submission_id=submitted["submissions"][-1]["submission_id"], decision="accept")
        submitted = action(1, child["task_id"], "submit", text="方案 A 分析完成")
        action(0, child["task_id"], "review", submission_id=submitted["submissions"][-1]["submission_id"], decision="accept")
        submitted = action(1, identifier, "submit", text="比较表完成")
        action(0, identifier, "review", submission_id=submitted["submissions"][-1]["submission_id"], decision="accept")
        action(0, identifier, "finish")
        for url in urls:
            for _ in range(5):
                post(url + "/v1/toy/flush", {})
        headers = {"X-Research-Admin": Path(workspace["admin_token_file"]).read_text()}
        events, cursor = [], 0
        while True:
            page = post(workspace["research_url"] + "/v1/research/query", {"after": cursor}, headers)
            if not page["events"]:
                break
            events.extend(page["events"])
            cursor = page["next_cursor"]
        kinds = sorted(set(e["kind"] for e in events))
        assert "private.incoming" in kinds and "group.sent" in kinds and "task.publish" in kinds
        assert len({e["event_id"] for e in events}) == len(events)
        result = {"nodes": 4, "independent_processes": True, "task_depth": 3, "summary_kind": "explicit-demo-not-real-model",
                  "trace_events": len(events), "duplicate_events": 0, "trace_kinds": kinds,
                  "timings": timings, "status": "passed", "limits": ["loopback, not cross-machine acceptance", "synthetic users and explicit demo summary", "no payment or trained policy"]}
        output = Path(output)
        (output / "benchmark-config.json").write_text(json.dumps({"nodes": 4, "depth": 3, "transport": "loopback-http", "research": "toy-full-content"}, indent=2))
        (output / "benchmark-results.json").write_text(json.dumps(result, indent=2))
        (output / "summary.md").write_text(f"# Toy Collaboration Benchmark\n\nFour independent nodes; three task levels; {len(events)} research events; zero duplicate IDs.\n\nPassed: trust, private/group communication, publication, summary, approval, subgroup consent, submission/review and collection.\n\nSummary is an explicit demo, not a real DSH result. Loopback only.\n")
        return result
    finally:
        stop(Path(workspace["output"]) / "workspace.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
