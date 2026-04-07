"""End-to-end integration test for the canonical task workflow:

    task -> claim -> artifact -> release -> summary

Validates the full lifecycle using HTTP against a live hub.
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from megahub.config import HubConfig
from megahub.server import create_server


def _req(base_url, method, path, payload=None):
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


class TestMegahubTaskWorkflow(unittest.TestCase):
    """Full task -> claim -> artifact -> release -> summary lifecycle."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "megahub.sqlite3")
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=self.db_path,
            presence_ttl_sec=120,
            log_events=False,
        )
        self.server = create_server(self.config)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.server.runtime.start()
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def _json(self, method, path, payload=None):
        return _req(self.base_url, method, path, payload)

    def _open_session(self, agent_id, **kwargs):
        payload = {"agent_id": agent_id, **kwargs}
        status, body = self._json("POST", "/v1/sessions", payload)
        self.assertEqual(status, 201, body)
        return body["result"]

    def test_task_claim_artifact_release_summary(self):
        """Complete task workflow exercising all message kinds and claims."""
        thread_id = "task-thread-001"

        session_a = self._open_session("agent-a", display_name="Agent A")
        session_b = self._open_session("agent-b", display_name="Agent B")

        # Agent A posts a task
        status, task_body = self._json("POST", "/v1/messages", {
            "from_agent": "agent-a",
            "channel": "general",
            "kind": "task",
            "body": "Implement the frobulator module",
            "thread_id": thread_id,
            "metadata": {"priority": "high", "estimated_hours": 2},
        })
        self.assertEqual(status, 201)
        task_msg = task_body["result"]
        self.assertEqual(task_msg["kind"], "task")
        self.assertEqual(task_msg["thread_id"], thread_id)
        task_id = task_msg["id"]

        # Agent B acquires a claim
        status, claim_body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "agent-b",
            "task_message_id": task_id,
            "thread_id": thread_id,
            "ttl_sec": 300,
            "metadata": {"approach": "TDD"},
        })
        self.assertEqual(status, 201)
        self.assertTrue(claim_body["acquired"])
        claim = claim_body["result"]
        self.assertEqual(claim["owner_agent_id"], "agent-b")
        self.assertIsNone(claim["released_at"])
        claim_key = claim["claim_key"]

        # Agent A tries to claim -- should fail
        status, race_body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "agent-a",
            "claim_key": claim_key,
            "thread_id": thread_id,
        })
        self.assertEqual(status, 200)
        self.assertFalse(race_body["acquired"])

        # Agent B posts artifact
        status, artifact_body = self._json("POST", "/v1/messages", {
            "from_agent": "agent-b",
            "channel": "general",
            "kind": "artifact",
            "body": "Frobulator implementation complete",
            "thread_id": thread_id,
            "reply_to": task_id,
            "attachments": [{
                "type": "code",
                "language": "python",
                "content": "def frobulate(x):\n    return x * 2\n",
            }],
            "metadata": {"tests_passing": True},
        })
        self.assertEqual(status, 201)
        artifact_msg = artifact_body["result"]
        self.assertEqual(artifact_msg["kind"], "artifact")
        self.assertEqual(artifact_msg["reply_to"], task_id)
        self.assertEqual(len(artifact_msg["attachments"]), 1)
        self.assertEqual(artifact_msg["attachments"][0]["type"], "code")

        # Agent B releases claim
        status, release_body = self._json("POST", "/v1/claims/release", {
            "claim_key": claim_key, "agent_id": "agent-b",
        })
        self.assertEqual(status, 200)
        self.assertIsNotNone(release_body["result"]["released_at"])

        # Agent A posts summary
        status, summary_body = self._json("POST", "/v1/messages", {
            "from_agent": "agent-a",
            "channel": "general",
            "kind": "notice",
            "body": "Task complete. Frobulator merged to main.",
            "thread_id": thread_id,
            "reply_to": task_id,
        })
        self.assertEqual(status, 201)
        self.assertEqual(summary_body["result"]["kind"], "notice")

        # Verify thread contains all 3 messages
        status, thread_body = self._json("GET", f"/v1/messages?thread_id={thread_id}")
        self.assertEqual(status, 200)
        thread_msgs = thread_body["result"]
        self.assertEqual(len(thread_msgs), 3)
        self.assertEqual(thread_msgs[0]["kind"], "task")
        self.assertEqual(thread_msgs[1]["kind"], "artifact")
        self.assertEqual(thread_msgs[2]["kind"], "notice")

        # Verify claims
        status, claims_body = self._json("GET", f"/v1/claims?thread_id={thread_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(claims_body["result"]), 1)
        self.assertIsNotNone(claims_body["result"][0]["released_at"])

        status, active_body = self._json("GET", f"/v1/claims?thread_id={thread_id}&active_only=true")
        self.assertEqual(status, 200)
        self.assertEqual(len(active_body["result"]), 0)

    def test_claim_message_post_artifact_with_file_ref(self):
        """Artifact with a file_ref attachment stores path and line range."""
        self._open_session("worker")

        status, artifact_body = self._json("POST", "/v1/messages", {
            "from_agent": "worker",
            "channel": "general",
            "kind": "artifact",
            "body": "Diff for review",
            "thread_id": "ref-thread",
            "attachments": [{
                "type": "diff_ref",
                "path": "src/frobulator.py",
                "base": "main",
                "head": "feature/frob",
                "description": "New frobulator module",
            }],
        })
        self.assertEqual(status, 201)
        att = artifact_body["result"]["attachments"][0]
        self.assertEqual(att["type"], "diff_ref")
        self.assertEqual(att["path"], "src/frobulator.py")
        self.assertEqual(att["base"], "main")

    def test_stale_claim_recovered_by_another_agent(self):
        """Convention 4: abandoned claims are recoverable after grace period."""
        self._open_session("agent-a")
        self._open_session("agent-b")

        status, claim_body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "agent-a",
            "claim_key": "stale-task",
            "thread_id": "recovery-thread",
            "ttl_sec": 5,
        })
        self.assertEqual(status, 201)

        self.server.runtime.store._conn.execute(
            "UPDATE claims SET expires_at = '2000-01-01T00:00:00Z' WHERE claim_key = 'stale-task'"
        )
        self.server.runtime.store._conn.commit()

        status, takeover = self._json("POST", "/v1/claims", {
            "owner_agent_id": "agent-b",
            "claim_key": "stale-task",
            "thread_id": "recovery-thread",
        })
        self.assertEqual(status, 201)
        self.assertTrue(takeover["acquired"])
        self.assertEqual(takeover["result"]["owner_agent_id"], "agent-b")

    def test_notice_used_for_status_updates(self):
        """Convention 5: notice kind for operational status updates."""
        self._open_session("agent-a")

        for body_text in ["Starting work", "50% done", "Wrapping up"]:
            status, msg = self._json("POST", "/v1/messages", {
                "from_agent": "agent-a",
                "channel": "general",
                "kind": "notice",
                "body": body_text,
                "thread_id": "progress-thread",
            })
            self.assertEqual(status, 201)
            self.assertEqual(msg["result"]["kind"], "notice")

        status, thread = self._json("GET", "/v1/messages?thread_id=progress-thread")
        self.assertEqual(status, 200)
        self.assertEqual(len(thread["result"]), 3)
        self.assertTrue(all(m["kind"] == "notice" for m in thread["result"]))


if __name__ == "__main__":
    unittest.main()
