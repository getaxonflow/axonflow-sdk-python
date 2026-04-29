#!/usr/bin/env python3
"""Cross-platform fake-agent + fake-checkpoint server for the heartbeat E2E.

Same as /tmp/heartbeat-real-e2e/server.py but vendored alongside the
per-SDK smokes so it can ship inside each SDK repo's tests/ directory.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

CHECKPOINT_HITS = 0
HEALTH_HITS = 0
LOCK = threading.Lock()
SDK_VERSION = "7.4.5"


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        global HEALTH_HITS
        if self.path == "/health":
            with LOCK:
                HEALTH_HITS += 1
            self._send_json(200, {"version": SDK_VERSION, "status": "ok"})
        elif self.path == "/__counter":
            with LOCK:
                self._send_json(
                    200, {"checkpoint_hits": CHECKPOINT_HITS, "health_hits": HEALTH_HITS}
                )
        else:
            self._send_json(503, {"error": "not implemented"})

    def do_POST(self):
        global CHECKPOINT_HITS
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        # Accept both /v1/ping (production path) and /v1/checkpoint (legacy local).
        if self.path.startswith("/v1/"):
            with LOCK:
                CHECKPOINT_HITS += 1
            self._send_json(200, {"latest_version": SDK_VERSION})
        else:
            self._send_json(503, {"error": "not implemented"})

    def log_message(self, *_args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    # Write port to a file so the launcher can read it (more reliable than
    # parsing stdout under Windows + various shells).
    with open(os.environ.get("HEARTBEAT_E2E_PORT_FILE", "port.txt"), "w") as f:
        f.write(str(port))
    print(f"SERVER_URL={base}", flush=True)
    sys.stdout.flush()

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        # Run for up to 5 minutes; CI orchestrator kills us when done.
        time.sleep(5 * 60)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
