import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from arc import FileRelayClient, FileRelayConfig, FileRelayServer, HubConfig, create_server, ensure_spool_dirs


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


class TestFileRelay(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "arc.sqlite3")
        self.spool_dir = os.path.join(self.tempdir.name, "relay")

        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=self.db_path,
            presence_ttl_sec=60,
            log_events=False,
        )
        self.server = create_server(self.config)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.server.runtime.start()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        self.relay = FileRelayServer(FileRelayConfig(
            base_url=self.base_url,
            spool_dir=self.spool_dir,
            poll_interval_sec=0.02,
            request_timeout_sec=5.0,
        ))
        self.relay_thread = threading.Thread(target=self.relay.run, daemon=True)
        self.relay_thread.start()

    def tearDown(self):
        self.relay.request_stop()
        self.relay_thread.join(timeout=2.0)
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_relay_roundtrip_opens_session_and_lists_agents(self):
        client = FileRelayClient(agent_id="sandbox-a", spool_dir=self.spool_dir, timeout=3.0, poll_interval_sec=0.02)

        open_resp = client.call("POST", "/v1/sessions", {"agent_id": "sandbox-a", "replace": True})
        self.assertTrue(open_resp["ok"], open_resp)
        self.assertEqual(open_resp["status"], 201)

        agents_resp = client.call("GET", "/v1/agents")
        self.assertTrue(agents_resp["ok"], agents_resp)
        self.assertEqual([item["agent_id"] for item in agents_resp["body"]["result"]], ["sandbox-a"])

    def test_relay_and_http_clients_share_messages_and_claims(self):
        relay_client = FileRelayClient(agent_id="sandbox-a", spool_dir=self.spool_dir, timeout=3.0, poll_interval_sec=0.02)

        relay_client.call("POST", "/v1/sessions", {"agent_id": "sandbox-a", "replace": True})
        status, session_resp = _req(self.base_url, "POST", "/v1/sessions", {"agent_id": "host-b", "replace": True})
        self.assertEqual(status, 201, session_resp)

        posted = relay_client.call("POST", "/v1/messages", {
            "from_agent": "sandbox-a",
            "channel": "general",
            "kind": "task",
            "body": "relay hello",
            "thread_id": "relay-thread-001",
        })
        self.assertTrue(posted["ok"], posted)
        task_id = posted["body"]["result"]["id"]

        status, thread_body = _req(self.base_url, "GET", "/v1/messages?thread_id=relay-thread-001")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in thread_body["result"]], [task_id])

        status, claim = _req(self.base_url, "POST", "/v1/claims", {
            "claim_key": "relay-claim-001",
            "owner_agent_id": "host-b",
            "thread_id": "relay-thread-001",
        })
        self.assertEqual(status, 201, claim)
        self.assertTrue(claim["acquired"])

        conflict = relay_client.call("POST", "/v1/claims", {
            "claim_key": "relay-claim-001",
            "owner_agent_id": "sandbox-a",
            "thread_id": "relay-thread-001",
        })
        self.assertTrue(conflict["body"]["ok"], conflict)
        self.assertEqual(conflict["status"], 200)
        self.assertFalse(conflict["body"]["acquired"])
        self.assertEqual(conflict["body"]["result"]["owner_agent_id"], "host-b")

    def test_relay_spool_is_append_only(self):
        client = FileRelayClient(agent_id="sandbox-a", spool_dir=self.spool_dir, timeout=3.0, poll_interval_sec=0.02)
        _req(self.base_url, "POST", "/v1/sessions", {"agent_id": "sandbox-a", "replace": True})

        resp = client.call("GET", "/v1/agents")
        self.assertTrue(resp["ok"], resp)

        response_dir = Path(self.spool_dir) / "responses" / "sandbox-a"
        work_dir = Path(self.spool_dir) / "requests" / "sandbox-a"
        self.assertTrue(any(path.suffix == ".json" for path in response_dir.iterdir()))
        self.assertTrue(any(path.suffix == ".work" for path in work_dir.iterdir()))

    def test_malformed_request_file_returns_structured_error_response(self):
        agent_dir = os.path.join(self.spool_dir, "requests", "bad-agent")
        os.makedirs(agent_dir, exist_ok=True)
        bad_request_path = os.path.join(agent_dir, "bad-request.json")
        with open(bad_request_path, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")

        response_path = os.path.join(self.spool_dir, "responses", "bad-agent", "bad-request.json")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if os.path.exists(response_path):
                break
            time.sleep(0.02)
        else:
            self.fail("relay did not emit an error response for malformed JSON")

        with open(response_path, encoding="utf-8") as handle:
            response = json.load(handle)
        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], 400)
        self.assertIn("invalid relay request", response["error"])

    def test_ensure_spool_dirs_uses_lexical_absolute_path(self):
        original_cwd = Path.cwd()
        os.chdir(self.tempdir.name)
        try:
            # POSIX getcwd() returns the canonical path, so on macOS
            # `tempdir.name` (e.g. /var/folders/...) and `Path.cwd()`
            # (e.g. /private/var/folders/...) differ even though they
            # name the same directory. Capture cwd *after* chdir so the
            # comparison is like-for-like; the contract under test is
            # that ensure_spool_dirs joins the relative path to cwd
            # without doing any extra .resolve() of its own.
            cwd = Path.cwd()
            root = ensure_spool_dirs(".arc-relay", agent_id="smoke-b")
        finally:
            os.chdir(original_cwd)
        self.assertEqual(root, cwd / ".arc-relay")
        self.assertTrue((root / "requests" / "smoke-b").exists())
        self.assertTrue((root / "responses" / "smoke-b").exists())

    def test_relay_client_ignores_permission_error_when_removing_response_file(self):
        client = FileRelayClient(agent_id="sandbox-a", spool_dir=self.spool_dir, timeout=3.0, poll_interval_sec=0.02)
        _req(self.base_url, "POST", "/v1/sessions", {"agent_id": "sandbox-a", "replace": True})

        original_unlink = Path.unlink

        def flaky_unlink(path: Path, *args, **kwargs):
            if path.name.endswith(".json") and "responses" in str(path):
                raise PermissionError("sandbox mount refused unlink")
            return original_unlink(path, *args, **kwargs)

        with mock.patch("pathlib.Path.unlink", autospec=True, side_effect=flaky_unlink):
            resp = client.call("GET", "/v1/agents")
        self.assertTrue(resp["ok"], resp)


if __name__ == "__main__":
    unittest.main()
