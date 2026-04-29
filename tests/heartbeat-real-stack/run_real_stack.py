#!/usr/bin/env python3
"""Cross-platform driver for the heartbeat real-stack E2E.

Stands up the fake checkpoint server (server.py), runs an SDK smoke
twice, and verifies:

  Cold (first run):  exactly 1 checkpoint hit, stamp file present
  Warm (second run): 0 additional checkpoint hits, stamp unchanged

Usage:
    python run_real_stack.py <smoke_command...>

The smoke command is invoked twice with these environment variables set:
    AXONFLOW_AGENT_URL
    AXONFLOW_CHECKPOINT_URL
    AXONFLOW_TELEMETRY=""        (defensive; CI workflows often default to off)
    HOME, USERPROFILE, LOCALAPPDATA, XDG_CACHE_HOME — all rerouted to a
                                  temp dir so the smoke can verify a clean
                                  cache state without touching the runner's
                                  real cache.

Exits 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def fetch_counter(url: str) -> dict:
    with urllib.request.urlopen(url + "/__counter", timeout=5) as r:
        return json.loads(r.read())


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: run_real_stack.py <smoke_command...>", file=sys.stderr)
        return 2

    smoke_cmd = list(argv[1:])
    # Java quirk: the JVM resolves user.home from native APIs at startup
    # and ignores $HOME on macOS / Windows. Detect and rewrite.
    java_target = any(
        "java" == os.path.basename(p).lower().replace(".exe", "") for p in [smoke_cmd[0]]
    )
    here = Path(__file__).resolve().parent
    server_script = here / "server.py"

    work_root = Path(tempfile.mkdtemp(prefix="hb-real-stack-"))
    port_file = work_root / "port.txt"

    env = os.environ.copy()
    env["HEARTBEAT_E2E_PORT_FILE"] = str(port_file)

    # Launch fake server.
    server_proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        # Wait for port file to appear. 60s is generous — macos-latest GitHub
        # runners can be slow to cold-start Python; 15s sometimes wasn't enough.
        # On any timeout we surface the server's stdout+stderr so the cause is
        # visible in CI logs instead of failing silently.
        deadline = time.time() + 60
        port_text: str | None = None
        while time.time() < deadline:
            if server_proc.poll() is not None:
                # Server crashed before writing the port file.
                stdout_bytes, _ = server_proc.communicate(timeout=2)
                output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                print(
                    f"FAIL: server exited with code {server_proc.returncode} before writing port file.\n"
                    f"--- server stdout/stderr ---\n{output}",
                    file=sys.stderr,
                )
                return 1
            if port_file.exists():
                port_text = port_file.read_text().strip()
                if port_text:
                    break
            time.sleep(0.1)
        if not port_text:
            # Port file timeout — kill server and surface its output.
            server_proc.terminate()
            try:
                stdout_bytes, _ = server_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                stdout_bytes, _ = server_proc.communicate()
            output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            print(
                "FAIL: server didn't write port file in 60s.\n"
                f"--- server stdout/stderr ---\n{output}",
                file=sys.stderr,
            )
            return 1

        port = int(port_text)
        server_url = f"http://127.0.0.1:{port}"
        print(f"server: {server_url}")

        # Sanity check.
        before = fetch_counter(server_url)
        assert before == {"checkpoint_hits": 0, "health_hits": 0}, before

        # Build a clean HOME / cache redirect.
        clean_home = work_root / "home"
        clean_home.mkdir()

        smoke_env = os.environ.copy()
        smoke_env.update(
            {
                "AXONFLOW_AGENT_URL": server_url,
                "AXONFLOW_CHECKPOINT_URL": f"{server_url}/v1/ping",
                "AXONFLOW_TELEMETRY": "",
                # Reroute cache dir on every OS:
                "HOME": str(clean_home),  # macOS, Linux
                "USERPROFILE": str(clean_home),  # Windows
                "LOCALAPPDATA": str(clean_home / "AppData" / "Local"),  # Windows
                "XDG_CACHE_HOME": str(clean_home / ".cache"),  # Linux
            }
        )
        # Ensure those nested dirs exist for Windows.
        Path(smoke_env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
        Path(smoke_env["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)

        # Java JVM ignores $HOME for user.home on macOS / Windows; pass
        # -Duser.home=<clean_home> as the first JVM arg.
        if java_target:
            smoke_cmd[1:1] = [f"-Duser.home={clean_home}"]

        # ---- Cold run ----
        print("--- cold run ---")
        cold = subprocess.run(smoke_cmd, env=smoke_env, capture_output=True, text=True)
        sys.stdout.write(cold.stdout)
        sys.stderr.write(cold.stderr)
        if cold.returncode != 0:
            print(f"FAIL: cold smoke exited {cold.returncode}", file=sys.stderr)
            return 1

        cold_counter = fetch_counter(server_url)
        print(f"counter after cold: {cold_counter}")
        if cold_counter["checkpoint_hits"] != 1:
            print(
                f"FAIL: expected 1 checkpoint hit on cold, got {cold_counter['checkpoint_hits']}",
                file=sys.stderr,
            )
            return 1

        # ---- Warm run (same clean_home, stamp now exists) ----
        print("--- warm run ---")
        warm = subprocess.run(smoke_cmd, env=smoke_env, capture_output=True, text=True)
        sys.stdout.write(warm.stdout)
        sys.stderr.write(warm.stderr)
        if warm.returncode != 0:
            print(f"FAIL: warm smoke exited {warm.returncode}", file=sys.stderr)
            return 1

        warm_counter = fetch_counter(server_url)
        print(f"counter after warm: {warm_counter}")
        delta = warm_counter["checkpoint_hits"] - cold_counter["checkpoint_hits"]
        if delta != 0:
            print(
                f"FAIL: expected 0 additional pings on warm run, got {delta}",
                file=sys.stderr,
            )
            return 1

        print("\nOK: cold + warm pass — heartbeat real-stack contract holds")
        return 0
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
