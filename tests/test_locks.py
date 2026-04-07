import json
import os
import tempfile
import threading
import time
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


class TestFileLocks(unittest.TestCase):
    """Tests for the POST/GET /v1/locks and POST /v1/locks/release endpoints."""

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

    def test_acquire_lock_basic(self):
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        self.assertEqual(status, 201)
        self.assertTrue(body["ok"])
        self.assertTrue(body["acquired"])
        lock = body["result"]
        self.assertEqual(lock["file_path"], "src/main.py")
        self.assertEqual(lock["agent_id"], "alpha")
        self.assertIsNone(lock["released_at"])

    def test_acquire_lock_conflict(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "src/main.py"
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["acquired"])
        self.assertEqual(body["result"]["agent_id"], "alpha")

    def test_acquire_lock_same_agent_refreshes(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py", "ttl_sec": 60
        })
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py", "ttl_sec": 600
        })
        self.assertEqual(status, 201)
        self.assertTrue(body["acquired"])
        self.assertEqual(body["result"]["agent_id"], "alpha")

    def test_acquire_lock_after_release(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "src/main.py"
        })
        self.assertEqual(status, 201)
        self.assertTrue(body["acquired"])
        self.assertEqual(body["result"]["agent_id"], "beta")

    def test_acquire_lock_after_expiry(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py", "ttl_sec": 5
        })
        time.sleep(6)
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "src/main.py"
        })
        self.assertEqual(status, 201)
        self.assertTrue(body["acquired"])
        self.assertEqual(body["result"]["agent_id"], "beta")

    def test_release_lock(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        status, body = self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNotNone(body["result"]["released_at"])

    def test_release_lock_not_owner(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        status, body = self._json("POST", "/v1/locks/release", {
            "agent_id": "beta", "file_path": "src/main.py"
        })
        self.assertEqual(status, 404)

    def test_release_nonexistent_lock(self):
        status, body = self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "does/not/exist.py"
        })
        self.assertEqual(status, 404)

    def test_release_already_released(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        status, body = self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "src/main.py"
        })
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["result"]["released_at"])

    def test_list_locks_empty(self):
        status, body = self._json("GET", "/v1/locks")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"], [])

    def test_list_locks_all(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "b.py"
        })
        status, body = self._json("GET", "/v1/locks")
        self.assertEqual(len(body["result"]), 2)

    def test_list_locks_by_agent(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "b.py"
        })
        status, body = self._json("GET", "/v1/locks?agent_id=alpha")
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["agent_id"], "alpha")

    def test_list_locks_active_only(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "b.py"
        })
        self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        status, body = self._json("GET", "/v1/locks?active_only=true")
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["agent_id"], "beta")

    def test_list_locks_agent_and_active(self):
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "b.py"
        })
        self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        status, body = self._json("GET", "/v1/locks?agent_id=alpha&active_only=true")
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["file_path"], "b.py")

    def test_acquire_lock_missing_agent_id(self):
        status, body = self._json("POST", "/v1/locks", {
            "file_path": "src/main.py"
        })
        self.assertEqual(status, 400)
        self.assertIn("agent_id", body["error"])

    def test_acquire_lock_missing_file_path(self):
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha"
        })
        self.assertEqual(status, 400)
        self.assertIn("file_path", body["error"])

    def test_acquire_lock_ttl_too_low(self):
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "x.py", "ttl_sec": 2
        })
        self.assertEqual(status, 400)
        self.assertIn("ttl_sec", body["error"])

    def test_acquire_lock_invalid_ttl(self):
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "x.py", "ttl_sec": "not_a_number"
        })
        self.assertEqual(status, 400)

    def test_acquire_lock_metadata_not_dict(self):
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "x.py", "metadata": "string"
        })
        self.assertEqual(status, 400)
        self.assertIn("metadata", body["error"])

    def test_acquire_lock_with_metadata(self):
        status, body = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "x.py",
            "metadata": {"reason": "editing"}
        })
        self.assertEqual(status, 201)
        self.assertEqual(body["result"]["metadata"], {"reason": "editing"})

    def test_release_lock_missing_file_path(self):
        status, body = self._json("POST", "/v1/locks/release", {
            "agent_id": "alpha"
        })
        self.assertEqual(status, 400)
        self.assertIn("file_path", body["error"])

    def test_release_lock_missing_agent_id(self):
        status, body = self._json("POST", "/v1/locks/release", {
            "file_path": "x.py"
        })
        self.assertEqual(status, 400)
        self.assertIn("agent_id", body["error"])

    def test_multiple_files_independent(self):
        s1, b1 = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "a.py"
        })
        s2, b2 = self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "b.py"
        })
        s3, b3 = self._json("POST", "/v1/locks", {
            "agent_id": "beta", "file_path": "c.py"
        })
        self.assertEqual(s1, 201)
        self.assertEqual(s2, 201)
        self.assertEqual(s3, 201)

    def test_lock_touches_agent_session(self):
        self._json("POST", "/v1/sessions", {
            "agent_id": "alpha", "display_name": "Alpha"
        })
        status1, agents1 = self._json("GET", "/v1/agents")
        first_seen = agents1["result"][0]["last_seen"]

        time.sleep(1.1)
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha", "file_path": "x.py"
        })
        status2, agents2 = self._json("GET", "/v1/agents")
        second_seen = agents2["result"][0]["last_seen"]
        self.assertGreater(second_seen, first_seen)


class TestDashboard(unittest.TestCase):
    """Tests for the GET / HTML dashboard endpoint."""

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

    def test_dashboard_returns_html(self):
        req = urllib.request.Request(f"{self.base_url}/", method="GET")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            content_type = resp.headers.get("Content-Type", "")
            self.assertIn("text/html", content_type)
            body = resp.read().decode("utf-8")
            self.assertIn("Megahub Dashboard", body)
            self.assertIn("/v1/agents", body)

    def test_dashboard_contains_sections(self):
        req = urllib.request.Request(f"{self.base_url}/", method="GET")
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            self.assertIn("Agents", body)
            self.assertIn("Claims", body)
            self.assertIn("Locks", body)
            self.assertIn("Active Threads", body)


class TestFileLocksClient(unittest.TestCase):
    """Tests for MegahubClient lock methods."""

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

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_client_acquire_lock(self):
        resp = self.client.acquire_lock("alpha", "src/app.py")
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["acquired"])
        self.assertEqual(resp["result"]["file_path"], "src/app.py")

    def test_client_release_lock(self):
        self.client.acquire_lock("alpha", "src/app.py")
        resp = self.client.release_lock("src/app.py", "alpha")
        self.assertTrue(resp["ok"])
        self.assertIsNotNone(resp["result"]["released_at"])

    def test_client_list_locks(self):
        self.client.acquire_lock("alpha", "a.py")
        self.client.acquire_lock("beta", "b.py")
        resp = self.client.list_locks()
        self.assertTrue(resp["ok"])
        self.assertEqual(len(resp["result"]), 2)

    def test_client_list_locks_filtered(self):
        self.client.acquire_lock("alpha", "a.py")
        self.client.acquire_lock("beta", "b.py")
        resp = self.client.list_locks(agent_id="alpha")
        self.assertEqual(len(resp["result"]), 1)

    def test_client_list_locks_active_only(self):
        self.client.acquire_lock("alpha", "a.py")
        self.client.acquire_lock("beta", "b.py")
        self.client.release_lock("a.py", "alpha")
        resp = self.client.list_locks(active_only=True)
        self.assertEqual(len(resp["result"]), 1)
        self.assertEqual(resp["result"][0]["agent_id"], "beta")

    def test_client_lock_conflict(self):
        self.client.acquire_lock("alpha", "x.py")
        resp = self.client.acquire_lock("beta", "x.py")
        self.assertTrue(resp["ok"])
        self.assertFalse(resp["acquired"])

    def test_client_lock_with_ttl_and_metadata(self):
        resp = self.client.acquire_lock(
            "alpha", "x.py",
            ttl_sec=60,
            metadata={"reason": "refactoring"},
        )
        self.assertTrue(resp["acquired"])
        self.assertEqual(resp["result"]["metadata"], {"reason": "refactoring"})

    def test_refresh_lock_endpoint_and_client(self):
        self.client.acquire_lock("alpha", "refresh.py", ttl_sec=60)

        status, body = _req(self.base_url, "POST", "/v1/locks/refresh", {
            "file_path": "refresh.py",
            "agent_id": "alpha",
            "ttl_sec": 300,
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["acquired"])

        resp = self.client.refresh_lock("refresh.py", "alpha", ttl_sec=300)
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["acquired"])

        status, body = _req(self.base_url, "POST", "/v1/locks/refresh", {
            "file_path": "missing.py",
            "agent_id": "alpha",
        })
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
