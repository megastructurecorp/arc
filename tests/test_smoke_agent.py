import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from arc import ArcClient, FileRelayConfig, FileRelayServer, HubConfig, create_server, run_smoke_agent


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


class TestSmokeAgent(unittest.TestCase):
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

    def test_smoke_roles_interoperate_across_http_and_relay(self):
        results: dict[str, int] = {}

        def run_role(role: str, transport: str):
            results[role] = run_smoke_agent(
                role=role,
                transport_name=transport,
                base_url=self.base_url,
                relay_dir=self.spool_dir,
                timeout_sec=10.0,
                poll_interval_sec=0.05,
            )

        threads = [
            threading.Thread(target=run_role, args=("smoke-a", "http"), daemon=True),
            threading.Thread(target=run_role, args=("smoke-b", "relay"), daemon=True),
            threading.Thread(target=run_role, args=("smoke-c", "http"), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(results, {"smoke-a": 0, "smoke-b": 0, "smoke-c": 0})

        status, resp = _req(self.base_url, "GET", "/v1/messages?channel=smoke-room&thread_id=smoke-relay-001&since_id=0")
        self.assertEqual(status, 200)
        messages = resp["result"]
        self.assertTrue(any(m["from_agent"] == "smoke-a" and m["kind"] == "task" for m in messages))
        self.assertTrue(any(m["from_agent"] == "smoke-b" and m["kind"] == "artifact" for m in messages))
        self.assertTrue(any(m["from_agent"] == "smoke-c" and m["kind"] == "notice" for m in messages))
        self.assertTrue(any(
            m["from_agent"] == "smoke-a"
            and m["kind"] == "notice"
            and "smoke test passed" in m["body"]
            for m in messages
        ))

    def test_relay_smoke_role_does_not_need_localhost_access(self):
        results: dict[str, int] = {}

        def run_http_role(role: str):
            results[role] = run_smoke_agent(
                role=role,
                transport_name="http",
                base_url=self.base_url,
                relay_dir=self.spool_dir,
                timeout_sec=10.0,
                poll_interval_sec=0.05,
            )

        def run_relay_role():
            results["smoke-b"] = run_smoke_agent(
                role="smoke-b",
                transport_name="relay",
                base_url="http://127.0.0.1:1",
                relay_dir=self.spool_dir,
                timeout_sec=10.0,
                poll_interval_sec=0.05,
            )

        threads = [
            threading.Thread(target=run_http_role, args=("smoke-a",), daemon=True),
            threading.Thread(target=run_relay_role, daemon=True),
            threading.Thread(target=run_http_role, args=("smoke-c",), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(results, {"smoke-a": 0, "smoke-b": 0, "smoke-c": 0})


class TestSessionRename(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=os.path.join(self.tempdir.name, "arc.sqlite3"),
            presence_ttl_sec=60,
            log_events=False,
        )
        self.server = create_server(self.config)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.server.runtime.start()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def _register(self, agent_id, display_name=None, replace=False):
        payload = {"agent_id": agent_id, "replace": replace}
        if display_name is not None:
            payload["display_name"] = display_name
        return _req(self.base_url, "POST", "/v1/sessions", payload)

    def test_rename_session_updates_display_name(self):
        status, resp = self._register("foo", "Foo")
        self.assertEqual(status, 201)
        status, resp = _req(self.base_url, "POST", "/v1/sessions/foo/rename", {"display_name": "Bar"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["agent_id"], "foo")
        self.assertEqual(resp["result"]["display_name"], "Bar")
        status, agents = _req(self.base_url, "GET", "/v1/agents")
        self.assertEqual(status, 200)
        rows = [a for a in agents["result"] if a["agent_id"] == "foo"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_name"], "Bar")

    def test_rename_unknown_agent_returns_404(self):
        status, resp = _req(self.base_url, "POST", "/v1/sessions/ghost/rename", {"display_name": "Nobody"})
        self.assertEqual(status, 404)
        self.assertFalse(resp["ok"])

    def test_rename_rejects_empty_or_oversized(self):
        status, _ = self._register("foo")
        self.assertEqual(status, 201)
        status, resp = _req(self.base_url, "POST", "/v1/sessions/foo/rename", {"display_name": ""})
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])
        status, resp = _req(self.base_url, "POST", "/v1/sessions/foo/rename", {"display_name": "x" * 65})
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_session_409_on_collision_without_replace(self):
        status, _ = self._register("foo")
        self.assertEqual(status, 201)
        status, resp = self._register("foo", replace=False)
        self.assertEqual(status, 409)
        self.assertFalse(resp["ok"])


class TestPollingBehavior(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=os.path.join(self.tempdir.name, "arc.sqlite3"),
            presence_ttl_sec=5,
            log_events=False,
        )
        self.server = create_server(self.config)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.server.runtime.start()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_events_long_poll_keeps_session_alive(self):
        status, resp = _req(self.base_url, "POST", "/v1/sessions", {"agent_id": "poller", "replace": True})
        self.assertEqual(status, 201, resp)

        for _ in range(3):
            start = time.time()
            status, resp = _req(self.base_url, "GET", "/v1/events?agent_id=poller&since_id=0&timeout=2&exclude_self=1")
            self.assertEqual(status, 200, resp)
            self.assertEqual(resp["result"], [])
            self.assertGreaterEqual(time.time() - start, 1.5)

        status, agents = _req(self.base_url, "GET", "/v1/agents")
        self.assertEqual(status, 200, agents)
        self.assertIn("poller", [row["agent_id"] for row in agents["result"]])

    def test_arc_client_poll_waits_longer_than_default_http_timeout(self):
        client = ArcClient("timeout-agent", base_url=self.base_url)
        client.register(replace=True)

        start = time.time()
        msgs = client.poll(timeout=16, exclude_self=True)
        elapsed = time.time() - start

        self.assertEqual(msgs, [])
        self.assertGreaterEqual(elapsed, 15.0)
        self.assertLess(elapsed, 22.0)


class TestHubInfoCapabilityNegotiation(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=os.path.join(self.tempdir.name, "arc.sqlite3"),
            log_events=False,
        )
        self.server = create_server(self.config)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.server.runtime.start()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_hub_info_advertises_normative_features_and_kinds(self):
        status, resp = _req(self.base_url, "GET", "/v1/hub-info")
        self.assertEqual(status, 200, resp)
        result = resp["result"]

        # Normative envelope fields per PROTOCOL.md §6.3.
        self.assertEqual(result["protocol_version"], "1")
        self.assertIn("instance_id", result)
        self.assertIn("features", result)
        self.assertIsInstance(result["features"], list)
        self.assertIn("message_kinds", result)
        self.assertIsInstance(result["message_kinds"], list)

        # The reference hub implements every v1 feature token.
        expected_features = {
            "sse",
            "relay",
            "long_poll_keepalive",
            "subtask_rollup",
            "rpc_kinds",
            "capability_filter",
            "shutdown_control",
            "session_rename",
        }
        self.assertEqual(set(result["features"]), expected_features)

        # message_kinds must be exactly the v1 closed set of 8.
        expected_kinds = {
            "chat", "notice", "task", "claim", "release",
            "artifact", "task_request", "task_result",
        }
        self.assertEqual(set(result["message_kinds"]), expected_kinds)

    def test_post_messages_rejects_unknown_kind(self):
        status, resp = _req(self.base_url, "POST", "/v1/sessions", {"agent_id": "kind-test", "replace": True})
        self.assertEqual(status, 201, resp)

        status, resp = _req(self.base_url, "POST", "/v1/messages", {
            "from_agent": "kind-test",
            "channel": "general",
            "kind": "status_update",
            "body": "ping",
        })
        self.assertEqual(status, 400, resp)
        self.assertFalse(resp["ok"])


if __name__ == "__main__":
    unittest.main()
