from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import HubConfig
from .file_relay import FileRelayConfig, run_file_relay
from .server import PIDFILE_NAME, _candidate_pidfiles, _read_pidfile, ensure_hub, run_server


def _safe_json_loads(raw: str, field_name: str = "metadata") -> dict:
    """Parse a JSON string with a helpful error message on failure."""
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON for --{field_name}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(result, dict):
        print(f"Error: --{field_name} must be a JSON object, got {type(result).__name__}", file=sys.stderr)
        sys.exit(2)
    return result


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": body}


def _parse_agents(raw: str) -> list[str]:
    agents = [item.strip() for item in raw.split(",") if item.strip()]
    if not agents:
        raise ValueError("--agents must include at least one agent id")
    return agents


def _slugify(value: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    compact = "-".join(part for part in lowered.split("-") if part)
    return compact or "task"


def _message_marks_completion(message: dict, agent_id: str) -> bool:
    if message.get("from_agent") != agent_id:
        return False
    metadata = message.get("metadata")
    if isinstance(metadata, dict) and metadata.get("complete") is True:
        return True
    kind = str(message.get("kind") or "").lower()
    if kind == "artifact":
        return True
    body = str(message.get("body") or "").lower()
    return any(token in body for token in ("complete", "completed", "done", "finished"))


def _time_ago(iso_ts: str) -> str:
    """Return a human-friendly relative time string like '2m ago'."""
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except (ValueError, TypeError):
        return iso_ts or "-"


def _truncate(text: str, max_len: int = 60) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_thread_list(threads: list[dict]) -> str:
    """Format a list of thread summaries as a human-readable table."""
    if not threads:
        return "No active threads."
    header = f"  {'THREAD':<36} {'STATUS':<11} {'TASKS':<8} {'CLAIMS':<8} {'LAST ACTIVITY'}"
    lines = [header]
    for t in threads:
        thread_id = t.get("thread_id", "?")
        status = t.get("status", "?")
        open_tasks = t.get("open_task_count", 0)
        total_tasks = t.get("total_task_count", 0)
        tasks_str = f"{open_tasks}/{total_tasks}"
        claims = t.get("active_claim_count", 0)
        last_ts = t.get("latest_message_ts")
        last_activity = _time_ago(last_ts) if last_ts else "-"
        lines.append(f"  {thread_id:<36} {status:<11} {tasks_str:<8} {claims:<8} {last_activity}")
    return "\n".join(lines)


def _format_thread_detail(detail: dict) -> str:
    """Format a thread detail response as a human-readable drill-down."""
    thread = detail.get("thread", {})
    messages = detail.get("messages", [])
    tasks = detail.get("tasks", [])
    claims = detail.get("claims", [])
    locks = detail.get("locks", [])

    lines: list[str] = []
    lines.append(f"  Thread: {thread.get('thread_id', '?')}")
    lines.append(f"  Channel: {thread.get('channel', '?')}")
    lines.append(f"  Status: {thread.get('status', '?')}")

    # Root task
    root_task_id = thread.get("root_task_id")
    if root_task_id is not None:
        root_msg = next((m for m in messages if m.get("id") == root_task_id), None)
        root_body = _truncate(root_msg["body"], 80) if root_msg else "?"
        lines.append(f"  Root Task: #{root_task_id} - {root_body}")

    # Tasks
    lines.append(f"")
    lines.append(f"  Tasks ({len(tasks)}):")
    if tasks:
        for task in tasks:
            tid = task.get("task_id", "?")
            status = task.get("status", "?")
            # Find the message that created this task
            task_msg = next((m for m in messages if m.get("id") == tid), None)
            body = _truncate(task_msg["body"], 60) if task_msg else ""
            parent = f" (child of #{task['parent_task_id']})" if task.get("parent_task_id") else ""
            lines.append(f"    #{tid} [{status}]{parent}  {body}")
    else:
        lines.append(f"    (none)")

    # Active claims
    now = datetime.now(timezone.utc)
    active_claims = []
    for c in claims:
        if c.get("released_at"):
            continue
        try:
            exp = datetime.fromisoformat(c["expires_at"].replace("Z", "+00:00"))
            if exp >= now:
                active_claims.append(c)
        except (ValueError, KeyError):
            pass

    lines.append(f"")
    lines.append(f"  Active Claims ({len(active_claims)}):")
    if active_claims:
        for c in active_claims:
            key = c.get("claim_key", "?")
            owner = c.get("owner_agent_id", "?")
            try:
                exp = datetime.fromisoformat(c["expires_at"].replace("Z", "+00:00"))
                remaining = exp - now
                mins = max(0, int(remaining.total_seconds()) // 60)
                lines.append(f"    {key}  owned by {owner}  expires in {mins}m")
            except (ValueError, KeyError):
                lines.append(f"    {key}  owned by {owner}")
    else:
        lines.append(f"    (none)")

    # Active locks
    active_locks = []
    for lock in locks:
        if lock.get("released_at"):
            continue
        try:
            exp = datetime.fromisoformat(lock["expires_at"].replace("Z", "+00:00"))
            if exp >= now:
                active_locks.append(lock)
        except (ValueError, KeyError):
            pass

    lines.append(f"")
    lines.append(f"  Active Locks ({len(active_locks)}):")
    if active_locks:
        for lock in active_locks:
            fp = lock.get("file_path", "?")
            agent = lock.get("agent_id", "?")
            lines.append(f"    {fp}  held by {agent}")
    else:
        lines.append(f"    (none)")

    # Latest artifact
    artifact_msgs = [m for m in messages if m.get("kind") == "artifact"]
    if artifact_msgs:
        latest = artifact_msgs[-1]
        lines.append(f"")
        lines.append(f"  Latest Artifact: #{latest['id']} - {_truncate(latest['body'], 60)} ({_time_ago(latest['ts'])})")

    # Last activity
    last_ts = thread.get("latest_message_ts")
    if last_ts:
        lines.append(f"  Last Activity: {_time_ago(last_ts)}")

    return "\n".join(lines)


def _format_replay(detail: dict) -> str:
    """Format a thread's message history as a chronological narrative."""
    thread = detail.get("thread", {})
    messages = detail.get("messages", [])
    claims = detail.get("claims", [])

    lines: list[str] = []
    lines.append(f"  === Thread: {thread.get('thread_id', '?')} ===")
    lines.append(f"")

    # Build a unified timeline: messages + claim events
    events: list[tuple[str, str]] = []

    for msg in messages:
        ts = msg.get("ts", "")
        time_str = ts[11:19] if len(ts) >= 19 else ts  # Extract HH:MM:SS
        agent = msg.get("from_agent", "?")
        kind = msg.get("kind", "chat").upper()
        body = _truncate(msg.get("body", ""), 72)

        entry_lines = [f"  [{time_str}] {agent} posted {kind}:"]
        entry_lines.append(f"    {body}")

        attachments = msg.get("attachments", [])
        if attachments:
            att_summary = ", ".join(
                f"1 {a.get('type', '?')}" + (f" ({a.get('language', '')})" if a.get("language") else "")
                for a in attachments
            )
            entry_lines.append(f"    Attachments: {att_summary}")

        events.append((ts, "\n".join(entry_lines)))

    for claim in claims:
        ts = claim.get("claimed_at", "")
        time_str = ts[11:19] if len(ts) >= 19 else ts
        owner = claim.get("owner_agent_id", "?")
        key = claim.get("claim_key", "?")
        task_id = claim.get("task_message_id")
        task_ref = f" task #{task_id}" if task_id else ""
        events.append((ts, f"  [{time_str}] {owner} CLAIMED{task_ref} (key: {key})"))

        if claim.get("released_at"):
            rel_ts = claim["released_at"]
            rel_time = rel_ts[11:19] if len(rel_ts) >= 19 else rel_ts
            events.append((rel_ts, f"  [{rel_time}] {owner} RELEASED claim {key}"))

    events.sort(key=lambda e: e[0])
    for _, text in events:
        lines.append(text)
        lines.append(f"")

    # Footer
    status = thread.get("status", "?")
    active_claims = thread.get("active_claim_count", 0)
    lines.append(f"  --- Thread {status}, {active_claims} active claim(s) ---")

    return "\n".join(lines)


def _run_status(base_url: str, storage: str, output_json: bool) -> int:
    """Gather hub status from pidfile + live API and display it."""
    # Try to find the pidfile
    pidfile_info = None
    for candidate in _candidate_pidfiles(storage):
        info = _read_pidfile(candidate)
        if info is not None:
            pidfile_info = info
            break

    # Try to reach the hub
    hub_reachable = False
    agents: list[dict] = []
    threads: list[dict] = []
    total_messages = 0
    db_path = None
    hub_info: dict | None = None
    instance_id = None

    try:
        resp = _request("GET", f"{base_url}/v1/agents")
        if resp.get("ok"):
            hub_reachable = True
            agents = resp.get("result", [])
    except Exception:
        pass

    if hub_reachable:
        try:
            resp = _request("GET", f"{base_url}/v1/threads")
            if resp.get("ok"):
                threads = resp.get("result", [])
                total_messages = sum(t.get("message_count", 0) for t in threads)
        except Exception:
            pass

        # Try hub-info endpoint (may not exist on older hubs)
        try:
            resp = _request("GET", f"{base_url}/v1/hub-info")
            if resp.get("ok"):
                hub_info = resp.get("result", {})
        except Exception:
            pass

        # Fall back to X-Megahub-Instance header for instance ID
        if hub_info:
            instance_id = hub_info.get("instance_id")
            if not db_path:
                db_path = hub_info.get("storage_path")
        if not instance_id:
            try:
                import urllib.request
                req = urllib.request.Request(f"{base_url}/v1/channels")
                with urllib.request.urlopen(req, timeout=3) as r:
                    instance_id = r.headers.get("X-Megahub-Instance")
            except Exception:
                pass

    if pidfile_info and not db_path:
        db_path = str(Path(pidfile_info["path"]).parent / storage)

    wal_mode = hub_info.get("wal_mode") if hub_info else None

    result = {
        "hub_url": base_url,
        "reachable": hub_reachable,
        "pid": pidfile_info["pid"] if pidfile_info else None,
        "pidfile": pidfile_info["path"] if pidfile_info else None,
        "storage": db_path or storage,
        "instance_id": instance_id,
        "wal_mode": wal_mode,
        "active_agents": len(agents),
        "agents": [a.get("agent_id", "?") for a in agents],
        "active_threads": len(threads),
        "total_messages": total_messages,
    }

    if output_json:
        print(json.dumps(result, indent=2))
        return 0

    if not hub_reachable:
        print(f"  Hub: NOT REACHABLE at {base_url}")
        if pidfile_info:
            print(f"  PID file: {pidfile_info['path']} (PID {pidfile_info['pid']})")
            print(f"  The hub process may have died. Try: megahub ensure")
        else:
            print(f"  No PID file found. The hub is not running.")
            print(f"  Start it with: megahub ensure")
        return 1

    print(f"  Hub: {base_url}")
    if pidfile_info:
        print(f"  PID: {pidfile_info['pid']}")
        print(f"  PID file: {pidfile_info['path']}")
    print(f"  Storage: {db_path or storage}")
    if instance_id:
        print(f"  Instance: {instance_id}")
    if wal_mode is not None:
        wal_str = "enabled" if wal_mode else "DISABLED (shared-filesystem may not work correctly)"
        print(f"  WAL mode: {wal_str}")
    print(f"  Active Agents ({len(agents)}): {', '.join(a.get('agent_id', '?') for a in agents) or '(none)'}")
    print(f"  Active Threads: {len(threads)}")
    print(f"  Total Messages: {total_messages}")
    return 0


def _run_orchestrate(args: argparse.Namespace) -> tuple[dict, int]:
    try:
        agents = _parse_agents(args.agents)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 2

    ensure_result = ensure_hub(
        host=args.host,
        port=args.port,
        storage=args.storage,
        timeout=args.ensure_timeout,
    )
    if not ensure_result.get("running"):
        return ensure_result, 1

    base_url = ensure_result["url"]
    dashboard_url = f"{base_url}/"
    stamp = int(time.time())
    channel = args.channel or f"orch-{_slugify(args.task)[:32]}-{stamp}"
    thread_id = args.thread_id or channel
    orchestrator_id = args.orchestrator_id or f"orchestrator-{stamp}"

    session_id: str | None = None
    try:
        session_resp = _request("POST", f"{base_url}/v1/sessions", {
            "agent_id": orchestrator_id,
            "display_name": "Megahub Orchestrator",
            "metadata": {"created_by": "megahub orchestrate", "thread_id": thread_id},
            "replace": True,
        })
        if not session_resp.get("ok"):
            return session_resp, 1
        session_id = session_resp["result"]["session_id"]

        channel_resp = _request("POST", f"{base_url}/v1/channels", {
            "name": channel,
            "created_by": orchestrator_id,
            "metadata": {"thread_id": thread_id, "agents": agents},
        })
        if not channel_resp.get("ok"):
            return channel_resp, 1

        task_resp = _request("POST", f"{base_url}/v1/messages", {
            "from_agent": orchestrator_id,
            "channel": channel,
            "kind": "task",
            "body": args.task,
            "thread_id": thread_id,
            "metadata": {"agents": agents, "orchestrated": True},
        })
        if not task_resp.get("ok"):
            return task_resp, 1
        task_id = task_resp["result"]["id"]

        dispatch_results: list[dict] = []
        for agent in agents:
            kickoff = _request("POST", f"{base_url}/v1/messages", {
                "from_agent": orchestrator_id,
                "to_agent": agent,
                "kind": "task",
                "body": (
                    f"Task: {args.task}\n"
                    f"Channel: {channel}\n"
                    f"Thread: {thread_id}\n"
                    f"Dashboard: {dashboard_url}\n"
                    "Post an artifact or a notice containing COMPLETE on this thread when finished."
                ),
                "thread_id": thread_id,
                "reply_to": task_id,
                "metadata": {"channel": channel, "dashboard_url": dashboard_url},
            })
            dispatch_results.append({"agent_id": agent, "ok": kickoff.get("ok", False)})

        pending = set(agents)
        completed: dict[str, int] = {}
        since_id = 0
        deadline = time.monotonic() + args.timeout
        while pending and time.monotonic() < deadline:
            resp = _request(
                "GET",
                f"{base_url}/v1/messages?{urllib.parse.urlencode({'thread_id': thread_id, 'since_id': since_id, 'limit': 500})}",
            )
            if resp.get("ok"):
                for message in resp["result"]:
                    message_id = message.get("id")
                    if isinstance(message_id, int):
                        since_id = max(since_id, message_id)
                    sender = message.get("from_agent")
                    if sender in pending and _message_marks_completion(message, sender):
                        pending.remove(sender)
                        completed[sender] = message_id or 0
            if pending:
                time.sleep(args.poll_interval_sec)

        result = {
            "ok": not pending,
            "base_url": base_url,
            "dashboard_url": dashboard_url,
            "channel": channel,
            "thread_id": thread_id,
            "task_message_id": task_id,
            "agents": agents,
            "dispatch_results": dispatch_results,
            "completed_agents": sorted(completed),
            "pending_agents": sorted(pending),
            "timed_out": bool(pending),
        }
        return result, 0 if not pending else 1
    finally:
        if session_id:
            _request("DELETE", f"{base_url}/v1/sessions/{session_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Megahub \u2014 local-first agent coordination hub")
    sub = parser.add_subparsers(dest="command", required=True)

    ensure = sub.add_parser("ensure", help="Ensure the hub is running (start if needed)")
    ensure.add_argument("--host", default="127.0.0.1")
    ensure.add_argument("--port", type=int, default=8765)
    ensure.add_argument("--storage", default="megahub.sqlite3")
    ensure.add_argument("--timeout", type=float, default=5.0)

    serve = sub.add_parser("serve", help="Run the hub daemon")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--storage", default="megahub.sqlite3")
    serve.add_argument("--presence-ttl", type=int, default=30)
    serve.add_argument("--allow-remote", action="store_true")
    serve.add_argument("--quiet-events", action="store_true")

    agents = sub.add_parser("agents", help="List active agents")
    agents.add_argument("--base-url", default="http://127.0.0.1:8765")

    channels = sub.add_parser("channels", help="List channels")
    channels.add_argument("--base-url", default="http://127.0.0.1:8765")

    create_channel = sub.add_parser("create-channel", help="Create a channel")
    create_channel.add_argument("name")
    create_channel.add_argument("--created-by")
    create_channel.add_argument("--base-url", default="http://127.0.0.1:8765")

    send = sub.add_parser("send", help="Send a message")
    send.add_argument("--base-url", default="http://127.0.0.1:8765")
    send.add_argument("--from-agent", required=True)
    send.add_argument("--channel")
    send.add_argument("--to-agent")
    send.add_argument("--kind", default="chat")
    send.add_argument("--body", default="")
    send.add_argument("--thread-id")
    send.add_argument("--reply-to", type=int)
    send.add_argument("--metadata", default="{}")

    messages = sub.add_parser("messages", help="Read channel or thread messages")
    messages.add_argument("channel", nargs="?")
    messages.add_argument("--thread-id")
    messages.add_argument("--base-url", default="http://127.0.0.1:8765")
    messages.add_argument("--since-id", type=int, default=0)
    messages.add_argument("--limit", type=int, default=100)

    inbox = sub.add_parser("inbox", help="Read an inbox")
    inbox.add_argument("agent_id")
    inbox.add_argument("--base-url", default="http://127.0.0.1:8765")
    inbox.add_argument("--since-id", type=int, default=0)
    inbox.add_argument("--limit", type=int, default=100)

    relay = sub.add_parser("relay", help="Forward file relay requests to a local Megahub HTTP hub")
    relay.add_argument("--base-url", default="http://127.0.0.1:8765")
    relay.add_argument("--spool-dir", default=".megahub-relay")
    relay.add_argument("--poll-interval-sec", type=float, default=0.25)
    relay.add_argument("--request-timeout-sec", type=float, default=30.0)

    orchestrate = sub.add_parser("orchestrate", help="Seed a coordinated task and wait for agent completion")
    orchestrate.add_argument("--task", required=True, help="Task description to post")
    orchestrate.add_argument("--agents", required=True, help="Comma-separated agent ids")
    orchestrate.add_argument("--channel", help="Optional channel name (defaults to derived task slug)")
    orchestrate.add_argument("--thread-id", help="Optional thread id (defaults to channel name)")
    orchestrate.add_argument("--orchestrator-id", help="Session id used by the CLI while coordinating")
    orchestrate.add_argument("--host", default="127.0.0.1")
    orchestrate.add_argument("--port", type=int, default=8765)
    orchestrate.add_argument("--storage", default="megahub.sqlite3")
    orchestrate.add_argument("--ensure-timeout", type=float, default=5.0)
    orchestrate.add_argument("--timeout", type=float, default=300.0)
    orchestrate.add_argument("--poll-interval-sec", type=float, default=1.0)

    claim = sub.add_parser("claim", help="Acquire a claim")
    claim.add_argument("--base-url", default="http://127.0.0.1:8765")
    claim.add_argument("--owner", required=True, help="Owner agent_id")
    claim.add_argument("--key", help="Claim key (derived from --task-message-id if omitted)")
    claim.add_argument("--task-message-id", type=int)
    claim.add_argument("--thread-id")
    claim.add_argument("--ttl", type=int, default=300)
    claim.add_argument("--metadata", default="{}")

    release = sub.add_parser("release", help="Release a claim")
    release.add_argument("--base-url", default="http://127.0.0.1:8765")
    release.add_argument("--key", required=True, help="Claim key to release")
    release.add_argument("--agent", required=True, help="Agent releasing the claim")

    claims = sub.add_parser("claims", help="List claims")
    claims.add_argument("--base-url", default="http://127.0.0.1:8765")
    claims.add_argument("--thread-id")
    claims.add_argument("--active-only", action="store_true")

    thread = sub.add_parser("thread", help="View thread list or thread detail")
    thread.add_argument("thread_id", nargs="?", help="Thread ID to inspect (omit for list)")
    thread.add_argument("--base-url", default="http://127.0.0.1:8765")
    thread.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")

    replay = sub.add_parser("replay", help="Replay a thread as a chronological narrative")
    replay.add_argument("--thread-id", required=True, help="Thread ID to replay")
    replay.add_argument("--base-url", default="http://127.0.0.1:8765")
    replay.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")

    status = sub.add_parser("status", help="Show hub status: URL, PID, storage, agents, threads")
    status.add_argument("--base-url", default="http://127.0.0.1:8765")
    status.add_argument("--storage", default="megahub.sqlite3")
    status.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")

    refresh_claim = sub.add_parser("refresh-claim", help="Refresh (extend TTL of) a held claim")
    refresh_claim.add_argument("--base-url", default="http://127.0.0.1:8765")
    refresh_claim.add_argument("--key", required=True, help="Claim key to refresh")
    refresh_claim.add_argument("--owner", required=True, help="Owner agent_id")
    refresh_claim.add_argument("--ttl", type=int, default=300, help="New TTL in seconds")

    refresh_lock = sub.add_parser("refresh-lock", help="Refresh (extend TTL of) a held file lock")
    refresh_lock.add_argument("--base-url", default="http://127.0.0.1:8765")
    refresh_lock.add_argument("--file-path", required=True, help="Locked file path")
    refresh_lock.add_argument("--agent", required=True, help="Agent holding the lock")
    refresh_lock.add_argument("--ttl", type=int, default=300, help="New TTL in seconds")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return _run_status(args.base_url, args.storage, args.output_json)

    if args.command == "ensure":
        result = ensure_hub(
            host=args.host, port=args.port,
            storage=args.storage, timeout=args.timeout,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("running") else 1

    if args.command == "serve":
        config = HubConfig(
            listen_host=args.host,
            port=args.port,
            storage_path=args.storage,
            log_events=not args.quiet_events,
            presence_ttl_sec=args.presence_ttl,
            allow_remote=args.allow_remote,
        )
        run_server(config)
        return 0

    if args.command == "relay":
        config = FileRelayConfig(
            base_url=args.base_url,
            spool_dir=args.spool_dir,
            poll_interval_sec=args.poll_interval_sec,
            request_timeout_sec=args.request_timeout_sec,
        )
        run_file_relay(config)
        return 0

    if args.command == "orchestrate":
        result, exit_code = _run_orchestrate(args)
        print(json.dumps(result, indent=2))
        return exit_code

    if args.command == "thread":
        if args.thread_id:
            encoded_id = urllib.parse.quote(args.thread_id, safe="")
            result = _request("GET", f"{args.base_url}/v1/threads/{encoded_id}")
            if args.output_json:
                print(json.dumps(result, indent=2))
                return 0
            if result.get("ok"):
                print(_format_thread_detail(result["result"]))
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
            return 0
        else:
            result = _request("GET", f"{args.base_url}/v1/threads")
            if args.output_json:
                print(json.dumps(result, indent=2))
                return 0
            if result.get("ok"):
                print(_format_thread_list(result["result"]))
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
            return 0

    if args.command == "replay":
        encoded_id = urllib.parse.quote(args.thread_id, safe="")
        result = _request("GET", f"{args.base_url}/v1/threads/{encoded_id}")
        if args.output_json:
            print(json.dumps(result, indent=2))
            return 0
        if result.get("ok"):
            print(_format_replay(result["result"]))
        else:
            print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
            return 1
        return 0

    if args.command == "agents":
        result = _request("GET", f"{args.base_url}/v1/agents")
    elif args.command == "channels":
        result = _request("GET", f"{args.base_url}/v1/channels")
    elif args.command == "create-channel":
        payload = {"name": args.name, "created_by": args.created_by, "metadata": {}}
        result = _request("POST", f"{args.base_url}/v1/channels", payload)
    elif args.command == "send":
        payload = {
            "from_agent": args.from_agent,
            "to_agent": args.to_agent,
            "channel": args.channel,
            "kind": args.kind,
            "body": args.body,
            "metadata": _safe_json_loads(args.metadata),
        }
        if args.thread_id:
            payload["thread_id"] = args.thread_id
        if args.reply_to is not None:
            payload["reply_to"] = args.reply_to
        result = _request("POST", f"{args.base_url}/v1/messages", payload)
    elif args.command == "messages":
        params: dict[str, str] = {"since_id": str(args.since_id), "limit": str(args.limit)}
        if args.channel:
            params["channel"] = args.channel
        if args.thread_id:
            params["thread_id"] = args.thread_id
        result = _request("GET", f"{args.base_url}/v1/messages?{urllib.parse.urlencode(params)}")
    elif args.command == "claim":
        payload = {"owner_agent_id": args.owner, "ttl_sec": args.ttl}
        if args.key:
            payload["claim_key"] = args.key
        if args.task_message_id is not None:
            payload["task_message_id"] = args.task_message_id
        if args.thread_id:
            payload["thread_id"] = args.thread_id
        payload["metadata"] = _safe_json_loads(args.metadata)
        result = _request("POST", f"{args.base_url}/v1/claims", payload)
    elif args.command == "release":
        payload = {"claim_key": args.key, "agent_id": args.agent}
        result = _request("POST", f"{args.base_url}/v1/claims/release", payload)
    elif args.command == "claims":
        claim_params: dict[str, str] = {}
        if args.thread_id:
            claim_params["thread_id"] = args.thread_id
        if args.active_only:
            claim_params["active_only"] = "true"
        qs = f"?{urllib.parse.urlencode(claim_params)}" if claim_params else ""
        result = _request("GET", f"{args.base_url}/v1/claims{qs}")
    elif args.command == "refresh-claim":
        payload = {
            "claim_key": args.key,
            "owner_agent_id": args.owner,
            "ttl_sec": args.ttl,
        }
        result = _request("POST", f"{args.base_url}/v1/claims/refresh", payload)
    elif args.command == "refresh-lock":
        payload = {
            "file_path": args.file_path,
            "agent_id": args.agent,
            "ttl_sec": args.ttl,
        }
        result = _request("POST", f"{args.base_url}/v1/locks/refresh", payload)
    else:
        encoded_id = urllib.parse.quote(args.agent_id, safe="")
        inbox_params = urllib.parse.urlencode({"since_id": str(args.since_id), "limit": str(args.limit)})
        result = _request(
            "GET",
            f"{args.base_url}/v1/inbox/{encoded_id}?{inbox_params}",
        )

    print(json.dumps(result, indent=2))
    return 0
