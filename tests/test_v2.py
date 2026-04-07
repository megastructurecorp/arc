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


class TestMegahubV2(unittest.TestCase):
    """Tests for v2 features: claims, thread queries, presence improvements."""

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

    def _json(self, method, path, payload=None):
        return _req(self.base_url, method, path, payload)

    def _open_session(self, agent_id, **kwargs):
        payload = {"agent_id": agent_id, **kwargs}
        status, body = self._json("POST", "/v1/sessions", payload)
        self.assertEqual(status, 201, body)
        return body["result"]

    def test_simultaneous_claim_attempts_only_one_winner(self):
        """Two agents racing for the same claim_key: only the first succeeds."""
        self._open_session("alpha")
        self._open_session("beta")

        status1, body1 = self._json("POST", "/v1/claims", {
            "claim_key": "task-100", "owner_agent_id": "alpha",
            "thread_id": "thread-race", "task_message_id": 100,
        })
        self.assertEqual(status1, 201)
        self.assertTrue(body1["acquired"])

        status2, body2 = self._json("POST", "/v1/claims", {
            "claim_key": "task-100", "owner_agent_id": "beta",
            "thread_id": "thread-race", "task_message_id": 100,
        })
        self.assertEqual(status2, 200)
        self.assertFalse(body2["acquired"])
        self.assertEqual(body2["result"]["owner_agent_id"], "alpha")

    def test_same_owner_can_refresh_claim(self):
        """Same agent re-acquiring its own claim refreshes the expiry."""
        self._open_session("alpha")

        status1, body1 = self._json("POST", "/v1/claims", {
            "claim_key": "task-200", "owner_agent_id": "alpha", "ttl_sec": 60,
        })
        self.assertEqual(status1, 201)
        first_expires = body1["result"]["expires_at"]

        status2, body2 = self._json("POST", "/v1/claims", {
            "claim_key": "task-200", "owner_agent_id": "alpha", "ttl_sec": 300,
        })
        self.assertEqual(status2, 201)
        self.assertTrue(body2["acquired"])
        self.assertNotEqual(body2["result"]["expires_at"], first_expires)

    def test_stale_claim_takeover(self):
        """An expired claim can be taken over by another agent."""
        self._open_session("alpha")
        self._open_session("beta")

        status1, body1 = self._json("POST", "/v1/claims", {
            "claim_key": "task-300", "owner_agent_id": "alpha", "ttl_sec": 5,
        })
        self.assertEqual(status1, 201)

        self.server.runtime.store._conn.execute(
            "UPDATE claims SET expires_at = '2000-01-01T00:00:00Z' WHERE claim_key = 'task-300'"
        )
        self.server.runtime.store._conn.commit()

        status2, body2 = self._json("POST", "/v1/claims", {
            "claim_key": "task-300", "owner_agent_id": "beta",
        })
        self.assertEqual(status2, 201)
        self.assertTrue(body2["acquired"])
        self.assertEqual(body2["result"]["owner_agent_id"], "beta")

    def test_release_then_reacquire(self):
        """After releasing a claim, another agent can acquire it."""
        self._open_session("alpha")
        self._open_session("beta")

        status, body = self._json("POST", "/v1/claims", {
            "claim_key": "task-400", "owner_agent_id": "alpha",
            "thread_id": "thread-release", "task_message_id": 400,
        })
        self.assertEqual(status, 201)

        status, body = self._json("POST", "/v1/claims", {
            "claim_key": "task-400", "owner_agent_id": "beta",
        })
        self.assertEqual(status, 200)
        self.assertFalse(body["acquired"])

        status, body = self._json("POST", "/v1/claims/release", {
            "claim_key": "task-400", "agent_id": "alpha",
        })
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["result"]["released_at"])

        status, body = self._json("POST", "/v1/claims", {
            "claim_key": "task-400", "owner_agent_id": "beta", "thread_id": "thread-release",
        })
        self.assertEqual(status, 201)
        self.assertTrue(body["acquired"])
        self.assertEqual(body["result"]["owner_agent_id"], "beta")

    def test_release_by_wrong_agent_fails(self):
        """Only the owner can release a claim."""
        self._open_session("alpha")
        self._open_session("beta")

        self._json("POST", "/v1/claims", {
            "claim_key": "task-500", "owner_agent_id": "alpha",
        })

        status, body = self._json("POST", "/v1/claims/release", {
            "claim_key": "task-500", "agent_id": "beta",
        })
        self.assertEqual(status, 404)

    def test_list_claims_by_thread(self):
        """GET /v1/claims?thread_id=... returns claims for that thread."""
        self._open_session("alpha")

        self._json("POST", "/v1/claims", {
            "claim_key": "t1-task-1", "owner_agent_id": "alpha",
            "thread_id": "thread-1", "task_message_id": 1,
        })
        self._json("POST", "/v1/claims", {
            "claim_key": "t2-task-2", "owner_agent_id": "alpha",
            "thread_id": "thread-2", "task_message_id": 2,
        })

        status, body = self._json("GET", "/v1/claims?thread_id=thread-1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["claim_key"], "t1-task-1")

    def test_list_claims_active_only(self):
        """GET /v1/claims?active_only=true excludes released claims."""
        self._open_session("alpha")

        self._json("POST", "/v1/claims", {"claim_key": "active-claim", "owner_agent_id": "alpha"})
        self._json("POST", "/v1/claims", {"claim_key": "released-claim", "owner_agent_id": "alpha"})
        self._json("POST", "/v1/claims/release", {"claim_key": "released-claim", "agent_id": "alpha"})

        status, body = self._json("GET", "/v1/claims?active_only=true")
        self.assertEqual(status, 200)
        keys = [c["claim_key"] for c in body["result"]]
        self.assertIn("active-claim", keys)
        self.assertNotIn("released-claim", keys)

    def test_claim_key_derived_from_task_message_id(self):
        """If claim_key is omitted, it's derived from task_message_id."""
        self._open_session("alpha")

        status, body = self._json("POST", "/v1/claims", {
            "owner_agent_id": "alpha", "task_message_id": 42, "thread_id": "thread-derive",
        })
        self.assertEqual(status, 201)
        self.assertEqual(body["result"]["claim_key"], "task-42")

    def test_get_messages_by_thread_id(self):
        """GET /v1/messages?thread_id=... returns only messages in that thread."""
        self._open_session("alpha")

        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "thread A msg 1", "thread_id": "thread-A"})
        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "thread B msg 1", "thread_id": "thread-B"})
        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "thread A msg 2", "thread_id": "thread-A"})
        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "no thread"})

        status, body = self._json("GET", "/v1/messages?thread_id=thread-A")
        self.assertEqual(status, 200)
        bodies = [m["body"] for m in body["result"]]
        self.assertEqual(bodies, ["thread A msg 1", "thread A msg 2"])

    def test_get_messages_by_thread_and_channel(self):
        """When both thread_id and channel are provided, both must match."""
        self._open_session("alpha")
        self._json("POST", "/v1/channels", {"name": "builds"})

        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "general thread-X", "thread_id": "thread-X"})
        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "builds", "body": "builds thread-X", "thread_id": "thread-X"})

        status, body = self._json("GET", "/v1/messages?thread_id=thread-X&channel=general")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["body"], "general thread-X")

    def test_get_messages_thread_with_since_id(self):
        """Thread query respects since_id."""
        self._open_session("alpha")

        _, msg1 = self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "first", "thread_id": "thread-since"})
        _, msg2 = self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "second", "thread_id": "thread-since"})
        first_id = msg1["result"]["id"]

        status, body = self._json("GET", f"/v1/messages?thread_id=thread-since&since_id={first_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["result"]), 1)
        self.assertEqual(body["result"][0]["body"], "second")

    def test_get_messages_requires_channel_or_thread(self):
        """GET /v1/messages without channel or thread_id returns 400."""
        status, body = self._json("GET", "/v1/messages")
        self.assertEqual(status, 400)
        self.assertIn("channel or thread_id", body["error"])

    def test_default_ttl_is_120(self):
        """Default presence_ttl_sec is now 120."""
        config = HubConfig()
        self.assertEqual(config.presence_ttl_sec, 120)

    def test_message_post_refreshes_last_seen(self):
        """Posting a message refreshes the sender's session last_seen."""
        session = self._open_session("alpha")

        self.server.runtime.store._conn.execute(
            "UPDATE sessions SET last_seen = '2000-01-01T00:00:00Z' WHERE session_id = ?",
            (session["session_id"],),
        )
        self.server.runtime.store._conn.commit()

        self._json("POST", "/v1/messages", {"from_agent": "alpha", "channel": "general", "body": "touch test"})

        updated = self.server.runtime.store.get_session(session["session_id"])
        self.assertNotEqual(updated["last_seen"], "2000-01-01T00:00:00Z")

    def test_claim_acquire_refreshes_last_seen(self):
        """Acquiring a claim refreshes the owner's session last_seen."""
        session = self._open_session("alpha")

        self.server.runtime.store._conn.execute(
            "UPDATE sessions SET last_seen = '2000-01-01T00:00:00Z' WHERE session_id = ?",
            (session["session_id"],),
        )
        self.server.runtime.store._conn.commit()

        self._json("POST", "/v1/claims", {"claim_key": "touch-test-claim", "owner_agent_id": "alpha"})

        updated = self.server.runtime.store.get_session(session["session_id"])
        self.assertNotEqual(updated["last_seen"], "2000-01-01T00:00:00Z")

    def test_claim_release_refreshes_last_seen(self):
        """Releasing a claim refreshes the releaser's session last_seen."""
        session = self._open_session("alpha")

        self._json("POST", "/v1/claims", {"claim_key": "release-touch", "owner_agent_id": "alpha"})

        self.server.runtime.store._conn.execute(
            "UPDATE sessions SET last_seen = '2000-01-01T00:00:00Z' WHERE session_id = ?",
            (session["session_id"],),
        )
        self.server.runtime.store._conn.commit()

        self._json("POST", "/v1/claims/release", {"claim_key": "release-touch", "agent_id": "alpha"})

        updated = self.server.runtime.store.get_session(session["session_id"])
        self.assertNotEqual(updated["last_seen"], "2000-01-01T00:00:00Z")

    def test_refresh_claim_endpoint_requires_existing_owned_active_claim(self):
        self._open_session("alpha")
        self._json("POST", "/v1/claims", {
            "claim_key": "refresh-me", "owner_agent_id": "alpha", "ttl_sec": 60,
        })

        status, body = self._json("POST", "/v1/claims/refresh", {
            "claim_key": "refresh-me", "owner_agent_id": "alpha", "ttl_sec": 300,
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["acquired"])

        status, body = self._json("POST", "/v1/claims/refresh", {
            "claim_key": "missing-claim", "owner_agent_id": "alpha",
        })
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
