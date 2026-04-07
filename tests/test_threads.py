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


class TestMegahubThreads(unittest.TestCase):
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

    def test_list_threads_summarizes_root_task_claim_artifact_and_lock(self):
        thread_id = "thread-summary"
        self._open_session("alpha")
        self._open_session("beta")

        status, root = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "channel": "general",
            "kind": "task",
            "body": "Root task",
            "thread_id": thread_id,
        })
        self.assertEqual(status, 201)
        root_task_id = root["result"]["id"]

        status, child = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "channel": "general",
            "kind": "task",
            "body": "Child task",
            "thread_id": thread_id,
            "parent_task_id": root_task_id,
        })
        self.assertEqual(status, 201)

        status, artifact = self._json("POST", "/v1/messages", {
            "from_agent": "beta",
            "channel": "general",
            "kind": "artifact",
            "body": "Delivered work",
            "thread_id": thread_id,
            "reply_to": root_task_id,
        })
        self.assertEqual(status, 201)

        status, direct = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "to_agent": "beta",
            "kind": "chat",
            "body": "Please review",
            "thread_id": thread_id,
        })
        self.assertEqual(status, 201)

        status, claim = self._json("POST", "/v1/claims", {
            "owner_agent_id": "beta",
            "task_message_id": root_task_id,
            "thread_id": thread_id,
        })
        self.assertEqual(status, 201)
        self.assertTrue(claim["acquired"])

        status, lock = self._json("POST", "/v1/locks", {
            "agent_id": "beta",
            "file_path": "src/app.py",
            "metadata": {"thread_id": thread_id},
        })
        self.assertEqual(status, 201)
        self.assertTrue(lock["acquired"])

        status, body = self._json("GET", "/v1/threads")
        self.assertEqual(status, 200)
        summary = next(item for item in body["result"] if item["thread_id"] == thread_id)

        self.assertEqual(summary["channel"], "general")
        self.assertEqual(summary["root_task_id"], root_task_id)
        self.assertEqual(summary["latest_message_id"], direct["result"]["id"])
        self.assertEqual(summary["latest_artifact_id"], artifact["result"]["id"])
        self.assertEqual(summary["message_count"], 4)
        self.assertEqual(summary["total_task_count"], 2)
        self.assertEqual(summary["open_task_count"], 2)
        self.assertEqual(summary["active_claim_count"], 1)
        self.assertEqual(summary["active_lock_count"], 1)
        self.assertEqual(summary["status"], "open")

    def test_get_thread_detail_includes_direct_messages_and_related_objects(self):
        thread_id = "thread-detail"
        self._open_session("alpha")
        self._open_session("beta")

        _, root = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "kind": "task",
            "body": "Implement thread detail",
            "thread_id": thread_id,
        })
        root_task_id = root["result"]["id"]

        _, direct = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "to_agent": "beta",
            "kind": "chat",
            "body": "Looking for review",
            "thread_id": thread_id,
        })
        direct_message_id = direct["result"]["id"]

        _, claim = self._json("POST", "/v1/claims", {
            "owner_agent_id": "beta",
            "task_message_id": root_task_id,
            "thread_id": thread_id,
        })
        claim_key = claim["result"]["claim_key"]

        _, lock = self._json("POST", "/v1/locks", {
            "agent_id": "beta",
            "file_path": "src/threads.py",
            "metadata": {"thread_id": thread_id},
        })
        lock_path = lock["result"]["file_path"]

        status, body = self._json("GET", f"/v1/threads/{thread_id}")
        self.assertEqual(status, 200)

        detail = body["result"]
        self.assertEqual(detail["thread"]["thread_id"], thread_id)
        self.assertEqual(detail["thread"]["root_task_id"], root_task_id)
        self.assertEqual([msg["id"] for msg in detail["messages"]], [root_task_id, direct_message_id])
        self.assertEqual([task["task_id"] for task in detail["tasks"]], [root_task_id])
        self.assertEqual([claim["claim_key"] for claim in detail["claims"]], [claim_key])
        self.assertEqual([lock["file_path"] for lock in detail["locks"]], [lock_path])

    def test_thread_status_waiting_then_completed(self):
        thread_id = "thread-lifecycle"
        self._open_session("alpha")

        _, root = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "kind": "task",
            "body": "Root lifecycle task",
            "thread_id": thread_id,
        })
        root_task_id = root["result"]["id"]

        status, child = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "kind": "task",
            "body": "Leaf task",
            "thread_id": thread_id,
            "parent_task_id": root_task_id,
        })
        self.assertEqual(status, 201)
        child_task_id = child["result"]["id"]

        status, body = self._json("GET", "/v1/threads")
        self.assertEqual(status, 200)
        waiting_summary = next(item for item in body["result"] if item["thread_id"] == thread_id)
        self.assertEqual(waiting_summary["status"], "waiting")
        self.assertEqual(waiting_summary["open_task_count"], 2)

        status, completed = self._json("POST", f"/v1/tasks/{child_task_id}/complete")
        self.assertEqual(status, 200)
        self.assertTrue(completed["parent_completed"])

        status, body = self._json("GET", f"/v1/threads/{thread_id}")
        self.assertEqual(status, 200)
        completed_summary = body["result"]["thread"]
        self.assertEqual(completed_summary["status"], "completed")
        self.assertEqual(completed_summary["open_task_count"], 0)
        self.assertEqual(completed_summary["root_task_id"], root_task_id)

    def test_list_tasks_can_filter_by_thread_id(self):
        self._open_session("alpha")

        status, one = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "kind": "task",
            "body": "Task one",
            "thread_id": "thread-one",
        })
        self.assertEqual(status, 201)

        status, two = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "kind": "task",
            "body": "Task two",
            "thread_id": "thread-two",
        })
        self.assertEqual(status, 201)

        status, body = self._json("GET", "/v1/tasks?thread_id=thread-one")
        self.assertEqual(status, 200)
        self.assertEqual([task["task_id"] for task in body["result"]], [one["result"]["id"]])
        self.assertNotIn(two["result"]["id"], [task["task_id"] for task in body["result"]])

    def test_events_feed_includes_broadcast_and_visible_direct_messages(self):
        self._open_session("alpha")
        self._open_session("beta")
        self._open_session("gamma")

        _, broadcast = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "channel": "general",
            "body": "Broadcast update",
        })
        _, direct_beta = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "to_agent": "beta",
            "kind": "task",
            "body": "Private task for beta",
        })
        _, direct_gamma = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "to_agent": "gamma",
            "kind": "task",
            "body": "Private task for gamma",
        })

        status, body = self._json("GET", "/v1/events?agent_id=beta&since_id=0&limit=100")
        self.assertEqual(status, 200)
        ids = [message["id"] for message in body["result"]]

        self.assertEqual(ids, [broadcast["result"]["id"], direct_beta["result"]["id"]])
        self.assertNotIn(direct_gamma["result"]["id"], ids)

    def test_events_feed_can_filter_by_thread_id(self):
        self._open_session("alpha")
        self._open_session("beta")

        _, thread_broadcast = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "channel": "general",
            "body": "Thread broadcast",
            "thread_id": "thread-events",
        })
        _, thread_direct = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "to_agent": "beta",
            "kind": "chat",
            "body": "Thread direct",
            "thread_id": "thread-events",
        })
        self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "channel": "general",
            "body": "Other thread broadcast",
            "thread_id": "thread-other",
        })

        status, body = self._json("GET", "/v1/events?agent_id=beta&thread_id=thread-events")
        self.assertEqual(status, 200)
        ids = [message["id"] for message in body["result"]]
        self.assertEqual(ids, [thread_broadcast["result"]["id"], thread_direct["result"]["id"]])

    def test_events_feed_requires_agent_id(self):
        status, body = self._json("GET", "/v1/events")
        self.assertEqual(status, 400)
        self.assertIn("agent_id", body["error"])


if __name__ == "__main__":
    unittest.main()
