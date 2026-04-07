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


class TestMegahubRecovery(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "megahub.sqlite3")
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=self.db_path,
            presence_ttl_sec=30,
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

    def test_expired_session_releases_claims_and_locks_and_posts_notices(self):
        thread_id = "recovery-thread"
        session = self._open_session("alpha")

        status, root = self._json("POST", "/v1/messages", {
            "from_agent": "alpha",
            "channel": "general",
            "kind": "task",
            "body": "Recover me",
            "thread_id": thread_id,
        })
        self.assertEqual(status, 201)
        task_id = root["result"]["id"]

        self._json("POST", "/v1/claims", {
            "owner_agent_id": "alpha",
            "task_message_id": task_id,
            "thread_id": thread_id,
        })
        self._json("POST", "/v1/locks", {
            "agent_id": "alpha",
            "file_path": "src/recovery.py",
            "metadata": {"thread_id": thread_id},
        })

        self.server.runtime.store._conn.execute(
            "UPDATE sessions SET last_seen = '2000-01-01T00:00:00Z' WHERE session_id = ?",
            (session["session_id"],),
        )
        self.server.runtime.store._conn.commit()

        expired = self.server.runtime.store.prune_expired_sessions(self.config.presence_ttl_sec)
        self.assertEqual(len(expired), 1)
        self.server.runtime._recover_expired_work(expired)

        status, claims = self._json("GET", f"/v1/claims?thread_id={thread_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(claims["result"]), 1)
        self.assertIsNotNone(claims["result"][0]["released_at"])

        status, locks = self._json("GET", "/v1/locks?agent_id=alpha")
        self.assertEqual(status, 200)
        self.assertEqual(len(locks["result"]), 1)
        self.assertIsNotNone(locks["result"][0]["released_at"])

        status, thread = self._json("GET", f"/v1/threads/{thread_id}")
        self.assertEqual(status, 200)
        notices = [m for m in thread["result"]["messages"] if m["kind"] == "notice" and m["from_agent"] == "system"]
        self.assertEqual(len(notices), 2)
        for notice in notices:
            self.assertTrue(notice["metadata"]["recovery"])
            self.assertEqual(notice["metadata"]["stale_agent_id"], "alpha")


if __name__ == "__main__":
    unittest.main()
