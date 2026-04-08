import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import megahub_single as single
from megahub.config import HubConfig
from megahub.file_relay import FileRelayClient, FileRelayConfig, FileRelayServer
from megahub.server import create_server as package_create_server


def _req(base_url, method, path, payload=None):
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers.items()), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, dict(exc.headers.items()), body


IMPLEMENTATIONS = (
    ("package", package_create_server),
    ("single", single.create_server),
)


class CanonicalParityTest(unittest.TestCase):
    def _start_server(self, factory, tempdir):
        config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=os.path.join(tempdir, "megahub.sqlite3"),
            presence_ttl_sec=60,
            log_events=False,
        )
        server = factory(config)
        server.runtime.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return config, server, f"http://127.0.0.1:{server.server_address[1]}", thread

    def test_http_contract_matches_canonical_core(self):
        for label, factory in IMPLEMENTATIONS:
            with self.subTest(implementation=label):
                tempdir = tempfile.TemporaryDirectory()
                try:
                    config, server, base_url, thread = self._start_server(factory, tempdir.name)

                    status, _, alpha = _req(base_url, "POST", "/v1/sessions", {"agent_id": "alpha", "replace": True})
                    self.assertEqual(status, 201, alpha)
                    status, _, beta = _req(base_url, "POST", "/v1/sessions", {"agent_id": "beta", "replace": True})
                    self.assertEqual(status, 201, beta)

                    status, _, task = _req(base_url, "POST", "/v1/messages", {
                        "from_agent": "alpha",
                        "channel": "general",
                        "kind": "task",
                        "body": "Parity task",
                        "thread_id": "parity-thread",
                    })
                    self.assertEqual(status, 201, task)
                    task_id = task["result"]["id"]

                    status, _, direct = _req(base_url, "POST", "/v1/messages", {
                        "from_agent": "alpha",
                        "to_agent": "beta",
                        "kind": "chat",
                        "body": "Private parity note",
                        "thread_id": "parity-thread",
                    })
                    self.assertEqual(status, 201, direct)

                    status, _, claim = _req(base_url, "POST", "/v1/claims", {
                        "owner_agent_id": "beta",
                        "task_message_id": task_id,
                        "thread_id": "parity-thread",
                        "ttl_sec": 60,
                    })
                    self.assertEqual(status, 201, claim)
                    self.assertTrue(claim["acquired"])

                    status, _, lock = _req(base_url, "POST", "/v1/locks", {
                        "agent_id": "beta",
                        "file_path": "src/parity.py",
                        "ttl_sec": 60,
                        "metadata": {"thread_id": "parity-thread"},
                    })
                    self.assertEqual(status, 201, lock)
                    self.assertTrue(lock["acquired"])

                    status, headers, info = _req(base_url, "GET", "/v1/hub-info")
                    self.assertEqual(status, 200, info)
                    self.assertEqual(info["result"]["storage_path"], os.path.realpath(config.storage_path))
                    self.assertEqual(info["result"]["instance_id"], headers["X-Megahub-Instance"])

                    status, _, events = _req(base_url, "GET", "/v1/events?agent_id=beta&thread_id=parity-thread&since_id=0&limit=100")
                    self.assertEqual(status, 200, events)
                    self.assertEqual(
                        [item["id"] for item in events["result"]],
                        [task_id, direct["result"]["id"]],
                    )

                    status, _, inbox = _req(base_url, "GET", "/v1/inbox/beta?since_id=0&limit=100")
                    self.assertEqual(status, 200, inbox)
                    self.assertEqual([item["id"] for item in inbox["result"]], [direct["result"]["id"]])

                    status, _, threads = _req(base_url, "GET", "/v1/threads")
                    self.assertEqual(status, 200, threads)
                    summary = next(item for item in threads["result"] if item["thread_id"] == "parity-thread")
                    self.assertEqual(summary["root_task_id"], task_id)
                    self.assertEqual(summary["active_claim_count"], 1)
                    self.assertEqual(summary["active_lock_count"], 1)
                    self.assertEqual(summary["status"], "open")

                    status, _, detail = _req(base_url, "GET", "/v1/threads/parity-thread")
                    self.assertEqual(status, 200, detail)
                    self.assertEqual([item["id"] for item in detail["result"]["messages"]], [task_id, direct["result"]["id"]])

                    status, _, tasks = _req(base_url, "GET", "/v1/tasks?thread_id=parity-thread")
                    self.assertEqual(status, 200, tasks)
                    self.assertEqual([item["task_id"] for item in tasks["result"]], [task_id])

                    status, _, refreshed_claim = _req(base_url, "POST", "/v1/claims/refresh", {
                        "claim_key": f"task-{task_id}",
                        "owner_agent_id": "beta",
                        "ttl_sec": 300,
                    })
                    self.assertEqual(status, 200, refreshed_claim)
                    self.assertTrue(refreshed_claim["acquired"])

                    status, _, refreshed_lock = _req(base_url, "POST", "/v1/locks/refresh", {
                        "file_path": "src/parity.py",
                        "agent_id": "beta",
                        "ttl_sec": 300,
                    })
                    self.assertEqual(status, 200, refreshed_lock)
                    self.assertTrue(refreshed_lock["acquired"])

                    status, _, completed = _req(base_url, "POST", f"/v1/tasks/{task_id}/complete")
                    self.assertEqual(status, 200, completed)
                    self.assertEqual(completed["result"]["status"], "done")

                    status, _, released_claim = _req(base_url, "POST", "/v1/claims/release", {
                        "claim_key": f"task-{task_id}",
                        "agent_id": "beta",
                    })
                    self.assertEqual(status, 200, released_claim)

                    status, _, released_lock = _req(base_url, "POST", "/v1/locks/release", {
                        "file_path": "src/parity.py",
                        "agent_id": "beta",
                    })
                    self.assertEqual(status, 200, released_lock)

                    status, _, final_detail = _req(base_url, "GET", "/v1/threads/parity-thread")
                    self.assertEqual(status, 200, final_detail)
                    self.assertEqual(final_detail["result"]["thread"]["status"], "completed")
                    self.assertEqual(final_detail["result"]["thread"]["open_task_count"], 0)
                finally:
                    if "server" in locals():
                        server.shutdown()
                        server.runtime.stop()
                        server.server_close()
                    tempdir.cleanup()

    def test_relay_roundtrip_matches_canonical_core(self):
        for label, factory in IMPLEMENTATIONS:
            with self.subTest(implementation=label):
                tempdir = tempfile.TemporaryDirectory()
                try:
                    config, server, base_url, thread = self._start_server(factory, tempdir.name)
                    spool_dir = os.path.join(tempdir.name, "relay")

                    relay = FileRelayServer(FileRelayConfig(
                        base_url=base_url,
                        spool_dir=spool_dir,
                        poll_interval_sec=0.02,
                        request_timeout_sec=5.0,
                    ))
                    relay_thread = threading.Thread(target=relay.run, daemon=True)
                    relay_thread.start()

                    relay_client = FileRelayClient(agent_id="sandbox-a", spool_dir=spool_dir, timeout=3.0, poll_interval_sec=0.02)
                    opened = relay_client.call("POST", "/v1/sessions", {"agent_id": "sandbox-a", "replace": True})
                    self.assertTrue(opened["ok"], opened)
                    self.assertEqual(opened["status"], 201)

                    status, _, host = _req(base_url, "POST", "/v1/sessions", {"agent_id": "host-b", "replace": True})
                    self.assertEqual(status, 201, host)

                    posted = relay_client.call("POST", "/v1/messages", {
                        "from_agent": "sandbox-a",
                        "channel": "general",
                        "kind": "task",
                        "body": "relay parity task",
                        "thread_id": "relay-parity",
                    })
                    self.assertTrue(posted["ok"], posted)
                    task_id = posted["body"]["result"]["id"]

                    status, _, via_http = _req(base_url, "GET", "/v1/messages?thread_id=relay-parity")
                    self.assertEqual(status, 200, via_http)
                    self.assertEqual([item["id"] for item in via_http["result"]], [task_id])

                    status, _, claim = _req(base_url, "POST", "/v1/claims", {
                        "claim_key": "relay-parity-claim",
                        "owner_agent_id": "host-b",
                        "thread_id": "relay-parity",
                    })
                    self.assertEqual(status, 201, claim)

                    denied = relay_client.call("POST", "/v1/claims", {
                        "claim_key": "relay-parity-claim",
                        "owner_agent_id": "sandbox-a",
                        "thread_id": "relay-parity",
                    })
                    self.assertEqual(denied["status"], 200)
                    self.assertFalse(denied["body"]["acquired"])
                    self.assertEqual(denied["body"]["result"]["owner_agent_id"], "host-b")
                finally:
                    if "relay" in locals():
                        relay.request_stop()
                        relay_thread.join(timeout=2.0)
                    if "server" in locals():
                        server.shutdown()
                        server.runtime.stop()
                        server.server_close()
                    tempdir.cleanup()
