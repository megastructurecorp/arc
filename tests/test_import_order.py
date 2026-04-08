import json
import subprocess
import sys
import textwrap
import unittest


class TestImportOrder(unittest.TestCase):
    def test_single_file_import_first_serves_full_dashboard(self):
        script = textwrap.dedent(
            """
            import json
            import os
            import tempfile
            import threading
            import urllib.request

            import megahub_single
            from megahub.config import HubConfig
            from megahub.server import create_server

            tempdir = tempfile.TemporaryDirectory()
            try:
                config = HubConfig(
                    listen_host="127.0.0.1",
                    port=0,
                    storage_path=os.path.join(tempdir.name, "megahub.sqlite3"),
                    presence_ttl_sec=60,
                    log_events=False,
                )
                server = create_server(config)
                server.runtime.start()
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f"http://127.0.0.1:{server.server_address[1]}/"
                with urllib.request.urlopen(url) as response:
                    body = response.read().decode("utf-8")
                print(json.dumps({
                    "has_active_threads": "Active Threads" in body,
                    "has_hub_info": "Hub Info" in body,
                }))
            finally:
                if "server" in locals():
                    server.shutdown()
                    server.runtime.stop()
                    server.server_close()
                tempdir.cleanup()
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout.strip())
        self.assertTrue(payload["has_active_threads"], payload)
        self.assertTrue(payload["has_hub_info"], payload)
