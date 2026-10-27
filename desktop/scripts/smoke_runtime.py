"""Exercise the packaged runtime without models, credentials, or user data."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener
import uuid


def smoke(runtime: Path) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    runtime = runtime.resolve()
    python = runtime / "python/python.exe"
    runner = runtime / "runner.py"
    env = os.environ.copy()
    env["PATH"] = str(runtime / "node") + os.pathsep + env["PATH"]
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="deeptutor-desktop-smoke-") as directory, ExitStack() as guards:
        # Reproduce a separately running Web deployment on both default ports.
        blockers = []
        for port in [8001, 3782]:
            listener = guards.enter_context(socket.socket())
            listener.bind(("0.0.0.0", port))
            listener.listen()
            blockers.append(listener)
        root = Path(directory)
        home = root / "运行数据"
        control = root / "control"
        workspace = root / "学习资料"
        workspace.mkdir()
        (workspace / "example.txt").write_text("Desktop smoke test", encoding="utf-8")
        instance = uuid.uuid4().hex
        log_path = root / "smoke.log"
        endpoints = None
        with log_path.open("w", encoding="utf-8") as log:
            child = subprocess.Popen([
                str(python), "-X", "utf8", "-u", str(runner), "serve", "--home", str(home),
                "--control", str(control), "--instance", instance,
            ], env=env, stdout=log, stderr=log)
            try:
                ready = control / f"{instance}.json"
                deadline = time.monotonic() + 240
                while not ready.exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError(log_path.read_text(encoding="utf-8", errors="replace"))
                    time.sleep(1)
                endpoints = json.loads(ready.read_text(encoding="utf-8"))
                from urllib.parse import urlsplit
                assert all(urlsplit(endpoints[key]).port not in {8001, 3782}
                           for key in ["frontend_url", "backend_url"]), endpoints
                for key in ["frontend_url", "backend_url"]:
                    with opener.open(endpoints[key], timeout=20) as response:
                        assert response.status == 200, key
                # Native path selection feeds the ordinary workspace registry.
                result = subprocess.check_output([
                    str(python), "-X", "utf8", str(runner), "add-workspace", "--home", str(home), "--folder", str(workspace),
                ], env=env, text=True, encoding="utf-8")
                data = json.loads(result)
                assert any(Path(row["path"]).resolve() == workspace.resolve() for row in data["workspaces"]), {
                    "expected": str(workspace.resolve()), "actual": [row["path"] for row in data["workspaces"]],
                }
                assert (workspace / "example.txt").read_text(encoding="utf-8") == "Desktop smoke test"
                # Check actual listeners for the owned service process tree.
                import psutil
                owned = {child.pid, *(p.pid for p in psutil.Process(child.pid).children(recursive=True))}
                listeners = [c for c in psutil.net_connections(kind="tcp") if c.pid in owned and c.status == "LISTEN"]
                assert len(listeners) >= 2, listeners
                assert all(c.laddr.ip in {"127.0.0.1", "::1"} for c in listeners), listeners
                print("PASS: frontend/API ready; Unicode workspace registered; listeners are loopback-only.")
            except Exception:
                print(log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                control.mkdir(exist_ok=True)
                (control / f"{instance}.stop").touch()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], check=False)
                    raise
            assert child.returncode == 0, child.returncode
            if endpoints:
                from urllib.parse import urlsplit
                for key in ["frontend_url", "backend_url"]:
                    port = urlsplit(endpoints[key]).port
                    with socket.socket() as probe:
                        probe.settimeout(1)
                        assert probe.connect_ex(("127.0.0.1", port)) != 0, f"Orphaned service on {port}"
            print("PASS: graceful shutdown leaves no server listeners.")
            for listener in blockers:
                with socket.create_connection(("127.0.0.1", listener.getsockname()[1]), timeout=1):
                    pass
            print("PASS: desktop automatically avoids occupied ports and preserves both existing listeners.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    smoke(parser.parse_args().runtime)
