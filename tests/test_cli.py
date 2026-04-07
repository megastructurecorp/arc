import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

from megahub import cli
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


class TestCliOrchestrate(unittest.TestCase):
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

    def test_orchestrate_posts_kickoffs_and_waits_for_completion(self):
        def complete_agents():
            time.sleep(0.05)
            _req(self.base_url, "POST", "/v1/messages", {
                "from_agent": "alpha",
                "channel": "project-room",
                "kind": "notice",
                "body": "COMPLETE: alpha done",
                "thread_id": "project-thread",
            })
            _req(self.base_url, "POST", "/v1/messages", {
                "from_agent": "beta",
                "channel": "project-room",
                "kind": "artifact",
                "body": "beta artifact",
                "thread_id": "project-thread",
            })

        worker = threading.Thread(target=complete_agents, daemon=True)
        worker.start()

        stdout = io.StringIO()
        with mock.patch("megahub.cli.ensure_hub", return_value={"running": True, "started": False, "url": self.base_url}):
            with redirect_stdout(stdout):
                exit_code = cli.main([
                    "orchestrate",
                    "--task", "Ship it",
                    "--agents", "alpha,beta",
                    "--channel", "project-room",
                    "--thread-id", "project-thread",
                    "--timeout", "2.0",
                    "--poll-interval-sec", "0.01",
                    "--storage", self.db_path,
                ])

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["dashboard_url"], f"{self.base_url}/")
        self.assertEqual(result["completed_agents"], ["alpha", "beta"])
        self.assertEqual(result["pending_agents"], [])

        status, inbox = _req(self.base_url, "GET", "/v1/inbox/alpha")
        self.assertEqual(status, 200)
        self.assertEqual(len(inbox["result"]), 1)
        self.assertIn("Dashboard", inbox["result"][0]["body"])
        self.assertIn("project-thread", inbox["result"][0]["body"])

        status, agents = _req(self.base_url, "GET", "/v1/agents")
        self.assertEqual(status, 200)
        self.assertEqual(agents["result"], [])

    def test_orchestrate_times_out_when_agents_do_not_report_completion(self):
        stdout = io.StringIO()
        with mock.patch("megahub.cli.ensure_hub", return_value={"running": True, "started": False, "url": self.base_url}):
            with redirect_stdout(stdout):
                exit_code = cli.main([
                    "orchestrate",
                    "--task", "Wait forever",
                    "--agents", "alpha",
                    "--channel", "timeout-room",
                    "--thread-id", "timeout-thread",
                    "--timeout", "0.1",
                    "--poll-interval-sec", "0.01",
                    "--storage", self.db_path,
                ])

        self.assertEqual(exit_code, 1)
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["pending_agents"], ["alpha"])


if __name__ == "__main__":
    unittest.main()
