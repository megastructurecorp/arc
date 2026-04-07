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


def _req_with_headers(base_url, method, path, payload=None):
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            parsed = json.loads(body) if body else None
            return resp.status, dict(resp.headers.items()), parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        parsed = json.loads(body) if body else None
        return exc.code, dict(exc.headers.items()), parsed


class TestMegahub(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "megahub.sqlite3")
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=self.db_path,
            presence_ttl_sec=5,
            log_events=False,
        )
        self._start_server()

    def tearDown(self):
        self._stop_server()
        self.tempdir.cleanup()

    def _start_server(self):
        self.server = create_server(self.config)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.server.runtime.start()
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def _stop_server(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.runtime.stop()
            self.server.server_close()

    def _json(self, method, path, payload=None):
        return _req(self.base_url, method, path, payload)

    def _raw(self, method, path, payload=None):
        return _req_with_headers(self.base_url, method, path, payload)

    def _open_session(self, agent_id, **kwargs):
        payload = {"agent_id": agent_id, **kwargs}
        status, body = self._json("POST", "/v1/sessions", payload)
        self.assertEqual(status, 201, body)
        return body["result"]

    def test_general_exists_and_custom_channel_can_be_created(self):
        status, body = self._json("GET", "/v1/channels")
        self.assertEqual(status, 200)
        names = [item["name"] for item in body["result"]]
        self.assertIn("general", names)
        self.assertIn("direct", names)

        status, body = self._json(
            "POST",
            "/v1/channels",
            {"name": "builders", "created_by": "alpha", "metadata": {"topic": "coordination"}},
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["result"]["name"], "builders")

        status, body = self._json("GET", "/v1/channels")
        self.assertEqual(status, 200)
        names = [item["name"] for item in body["result"]]
        self.assertIn("builders", names)
        self.assertIn("general", names)
        self.assertIn("direct", names)

    def test_duplicate_agent_requires_replace_or_expiry(self):
        first = self._open_session("alpha", display_name="Alpha")

        status, body = self._json("POST", "/v1/sessions", {"agent_id": "alpha"})
        self.assertEqual(status, 409)
        self.assertIn("active session", body["error"])

        self.server.runtime.store._conn.execute(
            "UPDATE sessions SET last_seen = '2000-01-01T00:00:00Z' WHERE session_id = ?",
            (first["session_id"],),
        )
        self.server.runtime.store._conn.commit()

        status, body = self._json("POST", "/v1/sessions", {"agent_id": "alpha"})
        self.assertEqual(status, 201)
        self.assertNotEqual(body["result"]["session_id"], first["session_id"])

    def test_channel_message_visible_to_all_via_polling(self):
        self._open_session("alpha")
        self._open_session("beta")

        status, body = self._json(
            "POST", "/v1/messages",
            {"from_agent": "alpha", "channel": "general", "kind": "chat", "body": "hello builders"},
        )
        self.assertEqual(status, 201)
        message_id = body["result"]["id"]

        status, body = self._json("GET", "/v1/messages?channel=general")
        self.assertEqual(status, 200)
        ids = [m["id"] for m in body["result"]]
        self.assertIn(message_id, ids)

    def test_direct_message_only_in_inbox_of_addressed_agent(self):
        self._open_session("alpha")
        self._open_session("beta")

        status, body = self._json(
            "POST", "/v1/messages",
            {
                "from_agent": "alpha",
                "to_agent": "beta",
                "kind": "task",
                "body": "take the parser fix",
                "attachments": [{"type": "file_ref", "path": "parser.py"}],
            },
        )
        self.assertEqual(status, 201)
        message_id = body["result"]["id"]

        status, inbox_beta = self._json("GET", "/v1/inbox/beta")
        self.assertEqual(status, 200)
        self.assertEqual([msg["id"] for msg in inbox_beta["result"]], [message_id])

        status, inbox_alpha = self._json("GET", "/v1/inbox/alpha")
        self.assertEqual(status, 200)
        self.assertEqual(inbox_alpha["result"], [])

    def test_since_id_filters_old_messages(self):
        self._open_session("alpha")

        status, first = self._json("POST", "/v1/messages", {"from_agent": "alpha", "body": "first"})
        self.assertEqual(status, 201)
        first_id = first["result"]["id"]

        status, second = self._json("POST", "/v1/messages", {"from_agent": "alpha", "body": "second"})
        self.assertEqual(status, 201)
        second_id = second["result"]["id"]

        status, body = self._json("GET", f"/v1/messages?channel=general&since_id={first_id}")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in body["result"]], [second_id])

    def test_messages_persist_across_restart(self):
        self._open_session("alpha")
        self._json("POST", "/v1/channels", {"name": "builders"})
        status, channel_msg = self._json(
            "POST", "/v1/messages",
            {"from_agent": "alpha", "channel": "builders", "body": "persist channel"},
        )
        self.assertEqual(status, 201)
        status, direct_msg = self._json(
            "POST", "/v1/messages",
            {"from_agent": "alpha", "to_agent": "beta", "body": "persist direct"},
        )
        self.assertEqual(status, 201)

        self._stop_server()
        self._start_server()

        status, body = self._json("GET", "/v1/messages?channel=builders")
        self.assertEqual(status, 200)
        self.assertEqual([msg["id"] for msg in body["result"]], [channel_msg["result"]["id"]])

        status, body = self._json("GET", "/v1/inbox/beta")
        self.assertEqual(status, 200)
        self.assertEqual([msg["id"] for msg in body["result"]], [direct_msg["result"]["id"]])

    def test_attachment_validation_and_storage(self):
        self._open_session("alpha")

        status, body = self._json(
            "POST", "/v1/messages",
            {
                "from_agent": "alpha",
                "body": "artifact attached",
                "kind": "artifact",
                "attachments": [
                    {"type": "code", "language": "python", "content": "print('hi')"},
                    {"type": "diff_ref", "path": "src/app.py", "base": "abc", "head": "def"},
                ],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["result"]["attachments"][0]["type"], "code")
        self.assertEqual(body["result"]["attachments"][1]["type"], "diff_ref")

        status, body = self._json(
            "POST", "/v1/messages",
            {
                "from_agent": "alpha",
                "body": "bad attachment",
                "attachments": [{"type": "binary_blob", "content": "..."}],
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("unsupported attachment type", body["error"])

    def test_remote_bind_requires_allow_remote(self):
        with self.assertRaises(ValueError):
            create_server(
                HubConfig(
                    listen_host="0.0.0.0",
                    allow_remote=False,
                    storage_path=os.path.join(self.tempdir.name, "blocked.sqlite3"),
                )
            )

        server = create_server(
            HubConfig(
                listen_host="0.0.0.0",
                allow_remote=True,
                storage_path=os.path.join(self.tempdir.name, "allowed.sqlite3"),
            )
        )
        server.runtime.store.close()
        server.server_close()

    def test_instance_header_is_stable_across_requests_and_restart(self):
        status, headers, body = self._raw("GET", "/v1/channels")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        first_instance = headers.get("X-Megahub-Instance")
        self.assertIsNotNone(first_instance)
        self.assertNotEqual(first_instance, "")

        status, headers, body = self._raw("GET", "/v1/agents")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(headers.get("X-Megahub-Instance"), first_instance)

        self._stop_server()
        self._start_server()

        status, headers, body = self._raw("GET", "/v1/channels")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(headers.get("X-Megahub-Instance"), first_instance)


if __name__ == "__main__":
    unittest.main()
