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


class TestSharedStorageMode(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "shared.sqlite3")
        self.resolved_db_path = os.path.realpath(self.db_path)
        self._servers = []
        self._threads = []

    def tearDown(self):
        for server in reversed(self._servers):
            server.shutdown()
            server.runtime.stop()
            server.server_close()
        self.tempdir.cleanup()

    def _start_server(self):
        config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=self.db_path,
            presence_ttl_sec=120,
            log_events=False,
        )
        server = create_server(config)
        server.runtime.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._servers.append(server)
        self._threads.append(thread)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _json(self, base_url, method, path, payload=None):
        return _req(base_url, method, path, payload)

    def _open_session(self, base_url, agent_id):
        status, body = self._json(base_url, "POST", "/v1/sessions", {"agent_id": agent_id})
        self.assertEqual(status, 201, body)
        return body["result"]

    def test_sessions_survive_second_hub_start_and_hub_info_matches(self):
        base_a = self._start_server()
        self._open_session(base_a, "alpha")

        base_b = self._start_server()

        status, body = self._json(base_b, "GET", "/v1/agents")
        self.assertEqual(status, 200)
        self.assertEqual([agent["agent_id"] for agent in body["result"]], ["alpha"])

        status, info_a = self._json(base_a, "GET", "/v1/hub-info")
        self.assertEqual(status, 200)
        status, info_b = self._json(base_b, "GET", "/v1/hub-info")
        self.assertEqual(status, 200)

        self.assertEqual(info_a["result"]["storage_path"], self.resolved_db_path)
        self.assertEqual(info_b["result"]["storage_path"], self.resolved_db_path)
        self.assertEqual(info_a["result"]["instance_id"], info_b["result"]["instance_id"])
        self.assertTrue(info_a["result"]["wal_mode"])
        self.assertEqual(info_a["result"]["journal_mode"], "wal")

    def test_messages_and_claims_are_shared_across_hub_processes(self):
        base_a = self._start_server()
        base_b = self._start_server()
        self._open_session(base_a, "alpha")
        self._open_session(base_b, "beta")

        status, posted = self._json(
            base_a,
            "POST",
            "/v1/messages",
            {
                "from_agent": "alpha",
                "channel": "general",
                "kind": "notice",
                "body": "shared-db hello",
                "thread_id": "shared-fs-001",
            },
        )
        self.assertEqual(status, 201)

        status, body = self._json(base_b, "GET", "/v1/messages?thread_id=shared-fs-001")
        self.assertEqual(status, 200)
        self.assertEqual([message["id"] for message in body["result"]], [posted["result"]["id"]])

        status, claim = self._json(
            base_a,
            "POST",
            "/v1/claims",
            {"claim_key": "shared-claim", "owner_agent_id": "alpha", "thread_id": "shared-fs-001"},
        )
        self.assertEqual(status, 201)
        self.assertTrue(claim["acquired"])

        status, conflict = self._json(
            base_b,
            "POST",
            "/v1/claims",
            {"claim_key": "shared-claim", "owner_agent_id": "beta", "thread_id": "shared-fs-001"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(conflict["acquired"])
        self.assertEqual(conflict["result"]["owner_agent_id"], "alpha")
