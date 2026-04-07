import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from megahub.bridge import MegahubBridge, BridgeConfig, message_matches, normalize_handler_output
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


class TestMegahubBridge(unittest.TestCase):
    def test_message_matches_filters_self_channel_and_thread(self):
        msg = {
            "id": 12,
            "from_agent": "claude-code",
            "channel": "general",
            "thread_id": "coord-thread-001",
        }

        self.assertTrue(message_matches(
            msg, agent_id="codex-agent", channels={"general"},
            thread_ids={"coord-thread-001"}, ignore_self=True,
        ))
        self.assertFalse(message_matches(
            msg, agent_id="claude-code", channels={"general"},
            thread_ids={"coord-thread-001"}, ignore_self=True,
        ))
        self.assertFalse(message_matches(
            msg, agent_id="codex-agent", channels={"testing"},
            thread_ids={"coord-thread-001"}, ignore_self=True,
        ))
        self.assertFalse(message_matches(
            msg, agent_id="codex-agent", channels={"general"},
            thread_ids={"other-thread"}, ignore_self=True,
        ))

    def test_normalize_handler_output_fills_defaults(self):
        output = json.dumps({"kind": "chat", "body": "Acknowledged."})
        payloads = normalize_handler_output(
            output, agent_id="codex-agent", default_channel="general",
            default_thread_id="coord-thread-001", default_reply_to=10,
        )
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["from_agent"], "codex-agent")
        self.assertEqual(payloads[0]["channel"], "general")
        self.assertEqual(payloads[0]["thread_id"], "coord-thread-001")
        self.assertEqual(payloads[0]["reply_to"], 10)

    def test_normalize_handler_output_accepts_message_list(self):
        output = json.dumps([
            {"body": "One"},
            {"body": "Two", "channel": "testing", "reply_to": 99},
        ])
        payloads = normalize_handler_output(
            output, agent_id="codex-agent", default_channel="general",
            default_thread_id=None, default_reply_to=12,
        )
        self.assertEqual([item["body"] for item in payloads], ["One", "Two"])
        self.assertEqual(payloads[0]["reply_to"], 12)
        self.assertEqual(payloads[1]["channel"], "testing")
        self.assertEqual(payloads[1]["reply_to"], 99)

    def test_builtin_round_robin_outputs_are_normalized_for_send(self):
        bridge = MegahubBridge(BridgeConfig(
            base_url="http://127.0.0.1:8765",
            agent_id="codex-agent",
            builtin_handler="round-robin",
            agent_name="CodexAgent",
            handler_style="concise",
        ))
        trigger = {
            "id": 25,
            "channel": "general",
            "thread_id": "coord-thread-010",
            "body": "Claude turn 1",
            "metadata": {
                "loop": {
                    "participants": ["claude-code", "codex-agent"],
                    "next_agent": "codex-agent",
                    "remaining_turns": 9,
                    "turn_number": 1,
                }
            },
        }
        responses = bridge._invoke_builtin_handler(trigger)
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["from_agent"], "codex-agent")
        self.assertEqual(responses[0]["channel"], "general")
        self.assertEqual(responses[0]["thread_id"], "coord-thread-010")
        self.assertEqual(responses[0]["reply_to"], 25)


class TestMegahubBridgeIntegration(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "megahub.sqlite3")
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
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_bridge_receives_message_and_handler_posts_reply(self):
        handler = os.path.join(
            os.path.dirname(__file__), "..", "megahub", "examples", "thread_reply_handler.py",
        )
        python = os.path.abspath(sys.executable).replace("\\", "/")
        handler = os.path.abspath(handler).replace("\\", "/")

        bridge = MegahubBridge(BridgeConfig(
            base_url=self.base_url,
            agent_id="bridge-agent",
            display_name="Bridge Agent",
            capabilities=["auto-reply"],
            channels={"general"},
            thread_ids={"coord-thread-test"},
            handler_command=f"{python} {handler} --agent-name BridgeAgent",
            poll_interval_sec=0.1,
        ))
        bridge_thread = threading.Thread(target=bridge.run, daemon=True)
        bridge_thread.start()

        try:
            self._wait_for_agent("bridge-agent")

            _req(self.base_url, "POST", "/v1/sessions", {
                "agent_id": "sender-agent", "display_name": "Sender Agent",
                "capabilities": ["test"],
            })

            status, message_resp = _req(self.base_url, "POST", "/v1/messages", {
                "from_agent": "sender-agent",
                "channel": "general",
                "kind": "chat",
                "body": "Hello bridge. Please reply on this thread.",
                "thread_id": "coord-thread-test",
            })
            self.assertEqual(status, 201, message_resp)
            trigger_id = message_resp["result"]["id"]

            reply = self._wait_for_reply(trigger_id)
            self.assertEqual(reply["from_agent"], "bridge-agent")
            self.assertEqual(reply["thread_id"], "coord-thread-test")
            self.assertEqual(reply["reply_to"], trigger_id)
            self.assertIn("BridgeAgent", reply["body"])
        finally:
            bridge.request_stop()
            bridge_thread.join(timeout=5.0)

    def _wait_for_agent(self, agent_id, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, resp = _req(self.base_url, "GET", "/v1/agents")
            if any(item["agent_id"] == agent_id for item in resp["result"]):
                return
            time.sleep(0.05)
        self.fail(f"agent {agent_id} did not become active")

    def _wait_for_reply(self, trigger_id, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, resp = _req(self.base_url, "GET", "/v1/messages?channel=general&since_id=0&limit=50")
            for item in resp["result"]:
                if item.get("reply_to") == trigger_id and item.get("from_agent") == "bridge-agent":
                    return item
            time.sleep(0.1)
        self.fail(f"no bridge reply found for trigger {trigger_id}")


if __name__ == "__main__":
    unittest.main()
