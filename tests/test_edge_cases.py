"""Tests for edge cases, error paths, and gaps in existing coverage.

Covers:
- Malformed / missing JSON on POST endpoints
- Missing and invalid required fields
- Body and attachment size limits
- DELETE session endpoint
- Replace session flow
- Invalid query parameters (since_id, limit)
- Claims without claim_key or task_message_id
- Release claim edge cases
- Channel validation (empty name, duplicate, nonexistent)
- Config validation edge cases
- MegahubClient wrapper
- reply_to type validation
- Content-Length / large payload guard
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from megahub.config import HubConfig
from megahub.server import create_server


def _req(base_url, method, path, payload=None, raw_body=None, headers=None):
    url = f"{base_url}{path}"
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
    else:
        data = None
    hdrs = {"Content-Type": "application/json"} if data else {}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"ok": False, "error": body}


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "megahub.sqlite3")
        self.config = HubConfig(
            listen_host="127.0.0.1",
            port=0,
            storage_path=self.db_path,
            presence_ttl_sec=120,
            log_events=False,
            max_body_chars=500,
            max_attachment_chars=200,
            max_attachments=2,
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

    def _json(self, method, path, payload=None, **kwargs):
        return _req(self.base_url, method, path, payload, **kwargs)

    def _open_session(self, agent_id, **kwargs):
        payload = {"agent_id": agent_id, **kwargs}
        status, body = self._json("POST", "/v1/sessions", payload)
        self.assertEqual(status, 201, body)
        return body["result"]

    # ── Session edge cases ──────────────────────────────────────────

    def test_create_session_missing_agent_id(self):
        status, body = self._json("POST", "/v1/sessions", {})
        self.assertEqual(status, 400)
        self.assertIn("agent_id", body["error"])

    def test_create_session_empty_agent_id(self):
        status, body = self._json("POST", "/v1/sessions", {"agent_id": "  "})
        self.assertEqual(status, 400)
        self.assertIn("agent_id", body["error"])

    def test_create_session_capabilities_not_list(self):
        status, body = self._json("POST", "/v1/sessions", {
            "agent_id": "alpha", "capabilities": "not-a-list",
        })
        self.assertEqual(status, 400)
        self.assertIn("capabilities", body["error"])

    def test_create_session_metadata_not_dict(self):
        status, body = self._json("POST", "/v1/sessions", {
            "agent_id": "alpha", "metadata": ["not", "a", "dict"],
        })
        self.assertEqual(status, 400)
        self.assertIn("metadata", body["error"])

    def test_create_session_replace_true(self):
        first = self._open_session("alpha")
        second_status, second_body = self._json("POST", "/v1/sessions", {
            "agent_id": "alpha", "replace": True,
        })
        self.assertEqual(second_status, 201)
        self.assertNotEqual(second_body["result"]["session_id"], first["session_id"])

    def test_delete_session(self):
        session = self._open_session("alpha")
        status, body = self._json("DELETE", f"/v1/sessions/{session['session_id']}")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["result"]["deleted"])

    def test_delete_session_not_found(self):
        status, body = self._json("DELETE", "/v1/sessions/nonexistent-uuid")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

    def test_delete_already_inactive_session(self):
        session = self._open_session("alpha")
        self._json("DELETE", f"/v1/sessions/{session['session_id']}")
        status, body = self._json("DELETE", f"/v1/sessions/{session['session_id']}")
        self.assertEqual(status, 404)

    # ── Message edge cases ──────────────────────────────────────────

    def test_post_message_missing_from_agent(self):
        status, body = self._json("POST", "/v1/messages", {"body": "hello"})
        self.assertEqual(status, 400)
        self.assertIn("from_agent", body["error"])

    def test_post_message_empty_from_agent(self):
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "  ", "body": "hello",
        })
        self.assertEqual(status, 400)
        self.assertIn("from_agent", body["error"])

    def test_post_message_invalid_kind(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello", "kind": "invalid_kind",
        })
        self.assertEqual(status, 400)
        self.assertIn("unsupported kind", body["error"])

    def test_post_message_empty_body_no_attachments(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "", "attachments": [],
        })
        self.assertEqual(status, 400)
        self.assertIn("body or attachments", body["error"])

    def test_post_message_body_exceeds_max(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "x" * 501,
        })
        self.assertEqual(status, 400)
        self.assertIn("body exceeds max size", body["error"])

    def test_post_message_to_nonexistent_channel(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "channel": "nonexistent", "body": "hello",
        })
        self.assertEqual(status, 400)
        self.assertIn("channel does not exist", body["error"])

    def test_post_message_metadata_not_dict(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello", "metadata": "not-a-dict",
        })
        self.assertEqual(status, 400)
        self.assertIn("metadata", body["error"])

    def test_post_message_invalid_reply_to(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello", "reply_to": "not-an-int",
        })
        self.assertEqual(status, 400)
        self.assertIn("reply_to", body["error"])

    def test_post_message_attachments_not_list(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello", "attachments": "bad",
        })
        self.assertEqual(status, 400)
        self.assertIn("attachments must be a list", body["error"])

    def test_post_message_too_many_attachments(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello",
            "attachments": [
                {"type": "text", "content": "a"},
                {"type": "text", "content": "b"},
                {"type": "text", "content": "c"},
            ],
        })
        self.assertEqual(status, 400)
        self.assertIn("too many attachments", body["error"])

    def test_post_message_attachment_missing_content(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello",
            "attachments": [{"type": "text"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("requires content", body["error"])

    def test_post_message_attachment_oversized_content(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello",
            "attachments": [{"type": "text", "content": "x" * 300}],
        })
        self.assertEqual(status, 400)
        self.assertIn("exceeds max size", body["error"])

    def test_post_message_attachment_file_ref_missing_path(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello",
            "attachments": [{"type": "file_ref"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("requires path", body["error"])

    def test_post_message_attachment_not_dict(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello",
            "attachments": ["not-a-dict"],
        })
        self.assertEqual(status, 400)
        self.assertIn("JSON objects", body["error"])

    def test_post_message_attachment_invalid_start_line(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "hello",
            "attachments": [{"type": "file_ref", "path": "foo.py", "start_line": "abc"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("start_line", body["error"])

    def test_post_message_channel_defaults_to_direct_when_to_agent(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "to_agent": "beta", "body": "dm",
        })
        self.assertEqual(status, 201)
        self.assertEqual(body["result"]["channel"], "direct")

    def test_post_message_channel_defaults_to_general(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "body": "broadcast",
        })
        self.assertEqual(status, 201)
        self.assertEqual(body["result"]["channel"], "general")

    # ── Query parameter edge cases ──────────────────────────────────

    def test_get_messages_invalid_since_id(self):
        status, body = self._json("GET", "/v1/messages?channel=general&since_id=abc")
        self.assertEqual(status, 400)
        self.assertIn("since_id", body["error"])

    def test_get_messages_invalid_limit(self):
        status, body = self._json("GET", "/v1/messages?channel=general&limit=abc")
        self.assertEqual(status, 400)
        self.assertIn("limit", body["error"])

    def test_get_messages_nonexistent_channel(self):
        status, body = self._json("GET", "/v1/messages?channel=nope")
        self.assertEqual(status, 404)
        self.assertIn("channel not found", body["error"])

    def test_get_messages_limit_capped_at_max(self):
        self._open_session("alpha")
        for i in range(3):
            self._json("POST", "/v1/messages", {
                "from_agent": "alpha", "body": f"msg {i}",
            })
        status, body = self._json("GET", "/v1/messages?channel=general&limit=9999")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body["result"]), self.config.max_query_limit)

    def test_get_inbox_invalid_since_id(self):
        status, body = self._json("GET", "/v1/inbox/alpha?since_id=xyz")
        self.assertEqual(status, 400)
        self.assertIn("since_id", body["error"])

    # ── Channel edge cases ──────────────────────────────────────────

    def test_create_channel_missing_name(self):
        status, body = self._json("POST", "/v1/channels", {})
        self.assertEqual(status, 400)
        self.assertIn("name", body["error"])

    def test_create_channel_empty_name(self):
        status, body = self._json("POST", "/v1/channels", {"name": "  "})
        self.assertEqual(status, 400)
        self.assertIn("name", body["error"])

    def test_create_channel_metadata_not_dict(self):
        status, body = self._json("POST", "/v1/channels", {
            "name": "test-ch", "metadata": 42,
        })
        self.assertEqual(status, 400)
        self.assertIn("metadata", body["error"])

    def test_create_duplicate_channel_returns_existing(self):
        status1, body1 = self._json("POST", "/v1/channels", {"name": "dup-ch", "created_by": "alpha"})
        self.assertEqual(status1, 201)
        status2, body2 = self._json("POST", "/v1/channels", {"name": "dup-ch", "created_by": "beta"})
        self.assertEqual(status2, 200)
        self.assertEqual(body2["result"]["created_by"], "alpha")

    # ── Claims edge cases ───────────────────────────────────────────

    def test_acquire_claim_missing_owner(self):
        status, body = self._json("POST", "/v1/claims", {"claim_key": "test"})
        self.assertEqual(status, 400)
        self.assertIn("owner_agent_id", body["error"])

    def test_acquire_claim_missing_key_and_message_id(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/claims", {"owner_agent_id": "alpha"})
        self.assertEqual(status, 400)
        self.assertIn("claim_key or task_message_id", body["error"])

    def test_acquire_claim_ttl_too_low(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "alpha", "claim_key": "test", "ttl_sec": 1,
        })
        self.assertEqual(status, 400)
        self.assertIn("ttl_sec", body["error"])

    def test_acquire_claim_metadata_not_dict(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "alpha", "claim_key": "test", "metadata": "bad",
        })
        self.assertEqual(status, 400)
        self.assertIn("metadata", body["error"])

    def test_acquire_claim_invalid_task_message_id(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "alpha", "task_message_id": "not-int",
        })
        self.assertEqual(status, 400)
        self.assertIn("task_message_id", body["error"])

    def test_release_claim_missing_claim_key(self):
        status, body = self._json("POST", "/v1/claims/release", {"agent_id": "alpha"})
        self.assertEqual(status, 400)
        self.assertIn("claim_key", body["error"])

    def test_release_claim_missing_agent_id(self):
        status, body = self._json("POST", "/v1/claims/release", {"claim_key": "test"})
        self.assertEqual(status, 400)
        self.assertIn("agent_id", body["error"])

    def test_release_nonexistent_claim(self):
        status, body = self._json("POST", "/v1/claims/release", {
            "claim_key": "nope", "agent_id": "alpha",
        })
        self.assertEqual(status, 404)

    def test_release_already_released_claim(self):
        self._open_session("alpha")
        self._json("POST", "/v1/claims", {"owner_agent_id": "alpha", "claim_key": "dup-rel"})
        self._json("POST", "/v1/claims/release", {"claim_key": "dup-rel", "agent_id": "alpha"})
        status, body = self._json("POST", "/v1/claims/release", {
            "claim_key": "dup-rel", "agent_id": "alpha",
        })
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["result"]["released_at"])

    # ── Malformed JSON ──────────────────────────────────────────────

    def test_post_sessions_malformed_json(self):
        status, body = self._json(
            "POST", "/v1/sessions", raw_body=b"not-json",
        )
        self.assertEqual(status, 400)
        self.assertIn("malformed JSON", body["error"])

    def test_post_messages_malformed_json(self):
        status, body = self._json(
            "POST", "/v1/messages", raw_body=b"{bad json",
        )
        self.assertEqual(status, 400)
        self.assertIn("malformed JSON", body["error"])

    def test_post_sessions_array_body(self):
        status, body = self._json(
            "POST", "/v1/sessions", raw_body=b'["array"]',
        )
        self.assertEqual(status, 400)
        self.assertIn("JSON object", body["error"])

    # ── 404 routes ──────────────────────────────────────────────────

    def test_get_unknown_route(self):
        status, body = self._json("GET", "/v1/nonexistent")
        self.assertEqual(status, 404)

    def test_post_unknown_route(self):
        status, body = self._json("POST", "/v1/nonexistent", {})
        self.assertEqual(status, 404)

    def test_delete_unknown_route(self):
        status, body = self._json("DELETE", "/v1/nonexistent")
        self.assertEqual(status, 404)

    # ── Empty channel name after strip ──────────────────────────────

    def test_post_message_empty_channel(self):
        self._open_session("alpha")
        status, body = self._json("POST", "/v1/messages", {
            "from_agent": "alpha", "channel": "  ", "body": "hello",
        })
        self.assertEqual(status, 400)
        self.assertIn("channel", body["error"])


class TestConfigValidation(unittest.TestCase):
    def test_port_out_of_range(self):
        with self.assertRaises(ValueError):
            HubConfig(port=-1).validate()
        with self.assertRaises(ValueError):
            HubConfig(port=70000).validate()

    def test_presence_ttl_too_low(self):
        with self.assertRaises(ValueError):
            HubConfig(presence_ttl_sec=2).validate()

    def test_max_body_chars_too_low(self):
        with self.assertRaises(ValueError):
            HubConfig(max_body_chars=0).validate()

    def test_max_attachment_chars_too_low(self):
        with self.assertRaises(ValueError):
            HubConfig(max_attachment_chars=0).validate()

    def test_max_query_limit_too_low(self):
        with self.assertRaises(ValueError):
            HubConfig(max_query_limit=0).validate()

    def test_valid_config_passes(self):
        HubConfig().validate()

    def test_remote_bind_various_hosts(self):
        with self.assertRaises(ValueError):
            HubConfig(listen_host="0.0.0.0", allow_remote=False).validate()
        HubConfig(listen_host="0.0.0.0", allow_remote=True).validate()
        HubConfig(listen_host="::1", allow_remote=False).validate()

    def test_storage_validation_creates_parent_directory(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            storage_path = os.path.join(tempdir.name, "nested", "dir", "megahub.sqlite3")
            HubConfig(storage_path=storage_path).validate()
            self.assertTrue(os.path.isdir(os.path.dirname(storage_path)))
        finally:
            tempdir.cleanup()

    def test_storage_validation_rejects_directory_path(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            with self.assertRaises(ValueError):
                HubConfig(storage_path=tempdir.name).validate()
        finally:
            tempdir.cleanup()

    def test_storage_validation_rejects_uncreatable_parent(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            blocker = os.path.join(tempdir.name, "blocker")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("x")
            storage_path = os.path.join(blocker, "nested", "megahub.sqlite3")
            with self.assertRaises(ValueError):
                HubConfig(storage_path=storage_path).validate()
        finally:
            tempdir.cleanup()


class TestMegahubClient(unittest.TestCase):
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

    def test_client_session_lifecycle(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        resp = client.open_session("test-agent", display_name="Test Agent")
        self.assertTrue(resp["ok"])
        session_id = resp["result"]["session_id"]

        agents = client.list_agents()
        self.assertTrue(agents["ok"])
        agent_ids = [a["agent_id"] for a in agents["result"]]
        self.assertIn("test-agent", agent_ids)

        close_resp = client.close_session(session_id)
        self.assertTrue(close_resp["ok"])

    def test_client_channel_and_messages(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        client.open_session("test-agent")

        ch = client.create_channel("test-ch", created_by="test-agent")
        self.assertTrue(ch["ok"])
        self.assertEqual(ch["result"]["name"], "test-ch")

        channels = client.list_channels()
        names = [c["name"] for c in channels["result"]]
        self.assertIn("test-ch", names)

        msg = client.send_message({
            "from_agent": "test-agent",
            "channel": "test-ch",
            "kind": "chat",
            "body": "hello via client",
        })
        self.assertTrue(msg["ok"])
        msg_id = msg["result"]["id"]

        msgs = client.get_messages("test-ch")
        self.assertTrue(msgs["ok"])
        ids = [m["id"] for m in msgs["result"]]
        self.assertIn(msg_id, ids)

    def test_client_get_hub_info(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        info = client.get_hub_info()
        self.assertTrue(info["ok"])
        self.assertEqual(info["result"]["storage_path"], os.path.realpath(self.db_path))
        self.assertEqual(info["result"]["journal_mode"], "wal")
        self.assertTrue(info["result"]["wal_mode"])

    def test_client_inbox(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        client.open_session("sender")
        client.open_session("receiver", replace=True)

        client.send_message({
            "from_agent": "sender",
            "to_agent": "receiver",
            "body": "direct msg",
        })

        inbox = client.get_inbox("receiver")
        self.assertTrue(inbox["ok"])
        self.assertEqual(len(inbox["result"]), 1)
        self.assertEqual(inbox["result"][0]["body"], "direct msg")

    def test_client_claims(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        client.open_session("claimer")

        acq = client.acquire_claim("claimer", claim_key="test-claim", thread_id="t1")
        self.assertTrue(acq["ok"])
        self.assertTrue(acq["acquired"])

        claims = client.list_claims(active_only=True)
        self.assertTrue(claims["ok"])
        keys = [c["claim_key"] for c in claims["result"]]
        self.assertIn("test-claim", keys)

        rel = client.release_claim("test-claim", "claimer")
        self.assertTrue(rel["ok"])
        self.assertIsNotNone(rel["result"]["released_at"])

    def test_client_context_manager(self):
        from megahub.client import MegahubClient

        with MegahubClient(self.base_url) as client:
            resp = client.list_channels()
            self.assertTrue(resp["ok"])

    def test_client_handles_http_error(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        resp = client.close_session("nonexistent")
        self.assertFalse(resp["ok"])

    def test_client_handles_connection_error(self):
        from megahub.client import MegahubClient

        client = MegahubClient("http://127.0.0.1:19999", timeout=1)
        resp = client.list_channels()
        self.assertFalse(resp["ok"])
        self.assertIn("connection error", resp["error"])

    def test_client_get_messages_with_thread_id(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        client.open_session("agent")
        client.send_message({
            "from_agent": "agent", "body": "threaded", "thread_id": "t-x",
        })
        msgs = client.get_messages(thread_id="t-x")
        self.assertTrue(msgs["ok"])
        self.assertEqual(len(msgs["result"]), 1)

    def test_client_raise_on_error_mode(self):
        from megahub.client import MegahubClient, MegahubError

        client = MegahubClient(self.base_url, raise_on_error=True)
        with self.assertRaises(MegahubError) as ctx:
            client.close_session("nonexistent")
        self.assertIn("not found", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 404)
        self.assertFalse(ctx.exception.response["ok"])

    def test_client_raise_on_error_connection_failure(self):
        from megahub.client import MegahubClient, MegahubError

        client = MegahubClient("http://127.0.0.1:19999", timeout=1, raise_on_error=True)
        with self.assertRaises(MegahubError) as ctx:
            client.list_channels()
        self.assertIn("connection error", str(ctx.exception))

    def test_client_refresh_claim(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        client.open_session("agent")
        client.acquire_claim("agent", claim_key="rc-1", ttl_sec=60)
        resp = client.refresh_claim("rc-1", "agent", ttl_sec=300)
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["acquired"])

    def test_client_wait_for_hub(self):
        from megahub.client import MegahubClient

        self.assertTrue(MegahubClient.wait_for_hub(self.base_url, timeout=3.0))

    def test_client_wait_for_hub_timeout(self):
        from megahub.client import MegahubClient

        self.assertFalse(MegahubClient.wait_for_hub(
            "http://127.0.0.1:19999", timeout=0.5, poll_interval=0.1,
        ))

    def test_client_tracks_last_instance_id(self):
        from megahub.client import MegahubClient

        client = MegahubClient(self.base_url)
        channels = client.list_channels()
        self.assertTrue(channels["ok"])
        self.assertIsNotNone(client.last_instance_id)
        self.assertEqual(
            client.last_response_headers.get("X-Megahub-Instance"),
            client.last_instance_id,
        )

        first_instance = client.last_instance_id
        agents = client.list_agents()
        self.assertTrue(agents["ok"])
        self.assertEqual(client.last_instance_id, first_instance)

    def test_megahub_error_attributes(self):
        from megahub.client import MegahubError

        err = MegahubError("test error", status=404, response={"ok": False, "error": "test error"})
        self.assertEqual(str(err), "test error")
        self.assertEqual(err.status, 404)
        self.assertEqual(err.response["error"], "test error")


if __name__ == "__main__":
    unittest.main()
