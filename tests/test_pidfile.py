import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import megahub_single as single
from megahub.config import HubConfig
from megahub.server import create_server, ensure_hub


class _DummyResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _req_json(base_url, method, path, payload=None):
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return resp.status, dict(resp.headers.items()), json.loads(resp.read().decode("utf-8"))


class TestPidfileSupport(unittest.TestCase):
    def test_package_server_writes_and_removes_pidfile(self):
        tempdir = tempfile.TemporaryDirectory()
        pidfile = os.path.join(tempdir.name, ".megahub.pid")
        server = create_server(HubConfig(port=0, storage_path=os.path.join(tempdir.name, "megahub.sqlite3"), log_events=False))
        try:
            server.runtime.start()
            with open(pidfile, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["pid"], os.getpid())
            self.assertEqual(payload["port"], server.server_address[1])
            self.assertEqual(payload["url"], f"http://127.0.0.1:{server.server_address[1]}")
            server.runtime.stop()
            server.server_close()
            self.assertFalse(os.path.exists(pidfile))
        finally:
            tempdir.cleanup()

    def test_package_ensure_hub_prefers_discovered_pidfile(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            pidfile = Path(tempdir.name) / ".megahub.pid"
            pidfile.write_text(json.dumps({
                "pid": 12345,
                "port": 9911,
                "url": "http://127.0.0.1:9911",
            }), encoding="utf-8")

            def fake_urlopen(request, timeout=2):
                if request.full_url == "http://127.0.0.1:9911/v1/channels":
                    return _DummyResponse()
                raise urllib.error.URLError("connection refused")

            with mock.patch("megahub.server.Path.cwd", return_value=Path(tempdir.name) / "nested" / "child"):
                with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    with mock.patch("subprocess.Popen") as popen:
                        result = ensure_hub(storage=os.path.join(tempdir.name, "megahub.sqlite3"))

            self.assertEqual(result, {"running": True, "started": False, "url": "http://127.0.0.1:9911"})
            popen.assert_not_called()
        finally:
            tempdir.cleanup()

    def test_single_file_server_writes_and_removes_pidfile(self):
        tempdir = tempfile.TemporaryDirectory()
        pidfile = os.path.join(tempdir.name, ".megahub.pid")
        config = single.HubConfig(port=0, storage_path=os.path.join(tempdir.name, "megahub.sqlite3"), log_events=False)
        server = single._Srv(config)
        try:
            server.start_prune()
            with open(pidfile, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["pid"], os.getpid())
            self.assertEqual(payload["port"], server.server_address[1])
            self.assertEqual(payload["url"], f"http://127.0.0.1:{server.server_address[1]}")
            server.stop()
            server.server_close()
            self.assertFalse(os.path.exists(pidfile))
        finally:
            tempdir.cleanup()

    def test_single_file_ensure_hub_prefers_discovered_pidfile(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            pidfile = Path(tempdir.name) / ".megahub.pid"
            pidfile.write_text(json.dumps({
                "pid": 54321,
                "port": 9922,
                "url": "http://127.0.0.1:9922",
            }), encoding="utf-8")

            def fake_urlopen(request, timeout=2):
                if request.full_url == "http://127.0.0.1:9922/v1/channels":
                    return _DummyResponse()
                raise urllib.error.URLError("connection refused")

            with mock.patch("megahub_single.Path.cwd", return_value=Path(tempdir.name) / "nested" / "child"):
                with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    with mock.patch("subprocess.Popen") as popen:
                        result = single.ensure_hub(storage=os.path.join(tempdir.name, "megahub.sqlite3"))

            self.assertEqual(result, {"running": True, "started": False, "url": "http://127.0.0.1:9922"})
            popen.assert_not_called()
        finally:
            tempdir.cleanup()

    def test_single_file_shared_storage_mode_preserves_sessions_and_reports_hub_info(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            db_path = os.path.join(tempdir.name, "shared.sqlite3")
            cfg_a = single.HubConfig(port=0, storage_path=db_path, log_events=False)
            cfg_b = single.HubConfig(port=0, storage_path=db_path, log_events=False)
            srv_a = single._Srv(cfg_a)
            srv_b = single._Srv(cfg_b)
            th_a = th_b = None
            try:
                srv_a.start_prune()
                th_a = threading.Thread(target=srv_a.serve_forever, daemon=True)
                th_a.start()

                base_a = f"http://127.0.0.1:{srv_a.bound_port}"
                status, _, body = _req_json(base_a, "POST", "/v1/sessions", {"agent_id": "alpha"})
                self.assertEqual(status, 201)
                self.assertEqual(body["result"]["agent_id"], "alpha")

                srv_b.start_prune()
                th_b = threading.Thread(target=srv_b.serve_forever, daemon=True)
                th_b.start()
                base_b = f"http://127.0.0.1:{srv_b.bound_port}"

                status, _, body = _req_json(base_b, "GET", "/v1/agents")
                self.assertEqual(status, 200)
                self.assertEqual([agent["agent_id"] for agent in body["result"]], ["alpha"])

                status, headers_a, info_a = _req_json(base_a, "GET", "/v1/hub-info")
                self.assertEqual(status, 200)
                status, headers_b, info_b = _req_json(base_b, "GET", "/v1/hub-info")
                self.assertEqual(status, 200)

                resolved = os.path.realpath(db_path)
                self.assertEqual(info_a["result"]["storage_path"], resolved)
                self.assertEqual(info_b["result"]["storage_path"], resolved)
                self.assertEqual(info_a["result"]["instance_id"], info_b["result"]["instance_id"])
                self.assertEqual(info_a["result"]["instance_id"], headers_a["X-Megahub-Instance"])
                self.assertEqual(info_b["result"]["instance_id"], headers_b["X-Megahub-Instance"])
                self.assertEqual(info_a["result"]["journal_mode"], "wal")
                self.assertTrue(info_a["result"]["wal_mode"])
            finally:
                srv_b.shutdown()
                srv_b.stop()
                srv_b.server_close()
                srv_a.shutdown()
                srv_a.stop()
                srv_a.server_close()
        finally:
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
