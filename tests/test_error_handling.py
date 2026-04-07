import http.client
import json
import os
import tempfile
import threading
import unittest

from megahub.config import HubConfig
from megahub.server import create_server


class TestMegahubErrorHandling(unittest.TestCase):
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
        self.server.runtime.start()
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runtime.stop()
        self.server.server_close()
        self.tempdir.cleanup()

    def _raw_request(self, method, path, *, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest(method, path)
            for key, value in (headers or {}).items():
                conn.putheader(key, value)
            conn.endheaders(body)
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            return resp.status, payload
        finally:
            conn.close()

    def test_malformed_json_returns_400(self):
        body = b'{"from_agent":"alpha","body":"oops"'
        status, payload = self._raw_request(
            "POST",
            "/v1/messages",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "malformed JSON")

    def test_invalid_content_length_returns_400(self):
        status, payload = self._raw_request(
            "POST",
            "/v1/sessions",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "abc",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Content-Length must be an integer")

    def test_oversized_request_rejected_before_parse(self):
        oversized_body = b" " * (
            self.config.max_body_chars
            + (self.config.max_attachment_chars * self.config.max_attachments)
            + 65_537
        )
        status, payload = self._raw_request(
            "POST",
            "/v1/messages",
            body=oversized_body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(oversized_body)),
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "request body exceeds max size")

    def test_reply_to_type_error_returns_400(self):
        body = json.dumps({
            "from_agent": "alpha",
            "body": "hello",
            "reply_to": {},
        }).encode("utf-8")
        status, payload = self._raw_request(
            "POST",
            "/v1/messages",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "reply_to must be an integer")

    def test_claim_ttl_type_error_returns_400(self):
        body = json.dumps({
            "owner_agent_id": "alpha",
            "claim_key": "bad-ttl",
            "ttl_sec": None,
        }).encode("utf-8")
        status, payload = self._raw_request(
            "POST",
            "/v1/claims",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "ttl_sec must be an integer")


if __name__ == "__main__":
    unittest.main()
