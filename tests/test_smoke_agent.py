import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from forge import FileRelayConfig, FileRelayServer, HubConfig, create_server, run_smoke_agent


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
        self.db_path = os.path.join(self.tempdir.name, "forge.sqlite3")
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


if __name__ == "__main__":
    unittest.main()
