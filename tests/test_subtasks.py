import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from megahub.config import HubConfig
from megahub.server import create_server
from megahub.client import MegahubClient


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


class TestStructuredSubtasks(unittest.TestCase):
    """Tests for the structured subtasks feature: parent_task_id, GET /v1/tasks, completion rollup."""

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
        self._json("POST", "/v1/channels", {"name": "work", "created_by": "test"})

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def _json(self, method, path, payload=None):
        return _req(self.base_url, method, path, payload)

    def _post_task(self, body, channel="work", parent_task_id=None, thread_id=None):
        payload = {
            "from_agent": "tester",
            "channel": channel,
            "kind": "task",
            "body": body,
        }
        if parent_task_id is not None:
            payload["parent_task_id"] = parent_task_id
        if thread_id is not None:
            payload["thread_id"] = thread_id
        status, resp = self._json("POST", "/v1/messages", payload)
        return resp["result"]

    def test_task_message_creates_task_record(self):
        msg = self._post_task("Build feature X")
        status, body = self._json("GET", "/v1/tasks")
        self.assertTrue(body["ok"])
        tasks = body["result"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], msg["id"])
        self.assertEqual(tasks[0]["status"], "open")
        self.assertIsNone(tasks[0]["parent_task_id"])

    def test_non_task_message_does_not_create_record(self):
        self._json("POST", "/v1/messages", {
            "from_agent": "tester", "channel": "work",
            "kind": "chat", "body": "Hello",
        })
        status, body = self._json("GET", "/v1/tasks")
        self.assertEqual(len(body["result"]), 0)

    def test_subtask_with_parent(self):
        parent = self._post_task("Parent task")
        child = self._post_task("Child task", parent_task_id=parent["id"])
        status, body = self._json("GET", "/v1/tasks")
        self.assertEqual(len(body["result"]), 2)
        child_task = [t for t in body["result"] if t["task_id"] == child["id"]][0]
        self.assertEqual(child_task["parent_task_id"], parent["id"])

    def test_list_tasks_by_parent_id(self):
        parent = self._post_task("Parent")
        c1 = self._post_task("Child 1", parent_task_id=parent["id"])
        c2 = self._post_task("Child 2", parent_task_id=parent["id"])
        self._post_task("Unrelated task")

        status, body = self._json("GET", f"/v1/tasks?parent_id={parent['id']}")
        self.assertEqual(len(body["result"]), 2)
        ids = {t["task_id"] for t in body["result"]}
        self.assertIn(c1["id"], ids)
        self.assertIn(c2["id"], ids)

    def test_list_tasks_by_status(self):
        t1 = self._post_task("Task 1")
        t2 = self._post_task("Task 2")
        self._json("POST", f"/v1/tasks/{t1['id']}/complete")

        status, body = self._json("GET", "/v1/tasks?status=open")
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["task_id"], t2["id"])

        status, body = self._json("GET", "/v1/tasks?status=done")
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["task_id"], t1["id"])

    def test_list_tasks_by_channel(self):
        self._json("POST", "/v1/channels", {"name": "other", "created_by": "test"})
        self._post_task("Task in work", channel="work")
        self._post_task("Task in other", channel="other")

        status, body = self._json("GET", "/v1/tasks?channel=work")
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["channel"], "work")

    def test_list_tasks_invalid_parent_id(self):
        status, body = self._json("GET", "/v1/tasks?parent_id=abc")
        self.assertEqual(status, 400)

    def test_list_tasks_invalid_status(self):
        status, body = self._json("GET", "/v1/tasks?status=invalid")
        self.assertEqual(status, 400)

    def test_complete_task(self):
        task = self._post_task("Task to complete")
        status, body = self._json("POST", f"/v1/tasks/{task['id']}/complete")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"]["status"], "done")
        self.assertIsNotNone(body["result"]["completed_at"])

    def test_complete_task_idempotent(self):
        task = self._post_task("Task")
        self._json("POST", f"/v1/tasks/{task['id']}/complete")
        status, body = self._json("POST", f"/v1/tasks/{task['id']}/complete")
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["status"], "done")

    def test_complete_nonexistent_task(self):
        status, body = self._json("POST", "/v1/tasks/99999/complete")
        self.assertEqual(status, 404)

    def test_completion_rollup(self):
        parent = self._post_task("Parent", thread_id="rollup-test")
        c1 = self._post_task("Sub 1", parent_task_id=parent["id"], thread_id="rollup-test")
        c2 = self._post_task("Sub 2", parent_task_id=parent["id"], thread_id="rollup-test")

        self._json("POST", f"/v1/tasks/{c1['id']}/complete")
        status, body = self._json("POST", f"/v1/tasks/{c2['id']}/complete")
        self.assertTrue(body.get("parent_completed"))

        status, parent_task = self._json("GET", "/v1/tasks?status=done")
        parent_ids = {t["task_id"] for t in parent_task["result"]}
        self.assertIn(parent["id"], parent_ids)

        status, msgs = self._json("GET", "/v1/messages?channel=work")
        rollup_msgs = [m for m in msgs["result"]
                       if m["kind"] == "notice" and "subtasks" in m["body"].lower()]
        self.assertTrue(len(rollup_msgs) > 0)

    def test_partial_completion_no_rollup(self):
        parent = self._post_task("Parent")
        c1 = self._post_task("Sub 1", parent_task_id=parent["id"])
        c2 = self._post_task("Sub 2", parent_task_id=parent["id"])

        status, body = self._json("POST", f"/v1/tasks/{c1['id']}/complete")
        self.assertNotIn("parent_completed", body)

        parent_task = self.server.runtime.store.get_task(parent["id"])
        self.assertEqual(parent_task["status"], "open")

    def test_task_preserves_channel_and_thread(self):
        task = self._post_task("Test", channel="work", thread_id="my-thread")
        status, body = self._json("GET", "/v1/tasks")
        t = body["result"][0]
        self.assertEqual(t["channel"], "work")
        self.assertEqual(t["thread_id"], "my-thread")

    def test_invalid_parent_task_id_in_message(self):
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "tester", "channel": "work",
            "kind": "task", "body": "Fail",
            "parent_task_id": "not_a_number",
        })
        self.assertEqual(status, 400)


class TestSubtasksClient(unittest.TestCase):
    """Tests for MegahubClient subtask methods."""

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
        self.client = MegahubClient(self.base_url)
        self.client.create_channel("work", created_by="test")

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_client_list_tasks(self):
        self.client.send_message({
            "from_agent": "a", "channel": "work", "kind": "task", "body": "Do it"
        })
        resp = self.client.list_tasks()
        self.assertTrue(resp["ok"])
        self.assertEqual(len(resp["result"]), 1)

    def test_client_list_tasks_with_filters(self):
        self.client.send_message({
            "from_agent": "a", "channel": "work", "kind": "task", "body": "T1"
        })
        resp = self.client.list_tasks(status="open", channel="work")
        self.assertEqual(len(resp["result"]), 1)

    def test_client_complete_task(self):
        msg = self.client.send_message({
            "from_agent": "a", "channel": "work", "kind": "task", "body": "T1"
        })
        task_id = msg["result"]["id"]
        resp = self.client.complete_task(task_id)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["status"], "done")

    def test_client_list_subtasks(self):
        parent = self.client.send_message({
            "from_agent": "a", "channel": "work", "kind": "task", "body": "Parent"
        })
        pid = parent["result"]["id"]
        self.client.send_message({
            "from_agent": "a", "channel": "work", "kind": "task",
            "body": "Child", "parent_task_id": pid,
        })
        resp = self.client.list_tasks(parent_id=pid)
        self.assertEqual(len(resp["result"]), 1)


if __name__ == "__main__":
    unittest.main()
