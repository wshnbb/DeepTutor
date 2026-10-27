"""Private desktop runtime entry point; not a network or arbitrary-command API."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import threading
import time


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def initialize(home: Path) -> None:
    os.environ["DEEPTUTOR_HOME"] = str(home)
    os.environ["DEEPTUTOR_VERSION_CHECK_ENABLED"] = "0"
    os.chdir(home)
    from deeptutor.runtime.cache_reset import reset_runtime_singletons
    from deeptutor.services.config import ensure_runtime_settings_files, export_runtime_settings_to_env
    from deeptutor.services.setup import init_user_directories

    reset_runtime_singletons()
    init_user_directories(home)
    ensure_runtime_settings_files()
    export_runtime_settings_to_env(overwrite=True)


def workspace_action(action: str, folder: str | None = None) -> dict:
    from deeptutor.services.config import load_auth_settings
    from deeptutor.services.workspace.service import ContentWorkspaceService

    # Never bypass account login or accidentally register under a different user.
    if load_auth_settings()["enabled"]:
        raise RuntimeError("已启用多用户登录，请在应用的设置 → 工作区中管理目录。")
    service = ContentWorkspaceService()
    if action == "add-workspace":
        root = Path(folder or "").resolve(strict=True)
        if not root.is_dir() or root.parent == root:
            raise ValueError("请选择资料文件夹，而不是整个磁盘。")
        service.create_workspace(root.name, str(root))
    return {"workspaces": service.list_workspaces(), "security_level": service.security_level()}


def serve(home: Path, control: Path, instance: str, gate: Path | None = None) -> None:
    if gate is not None:
        deadline = time.monotonic() + 30
        while not gate.exists():
            if time.monotonic() > deadline:
                raise TimeoutError("Desktop process ownership was not established.")
            time.sleep(0.05)
        gate.unlink()
    stop_file = control / f"{instance}.stop"
    ready_file = control / f"{instance}.json"
    # Reuse upstream's graceful stop polling and child-tree shutdown.
    os.environ["DEEPTUTOR_DETACHED_WORKER"] = "1"
    os.environ["DEEPTUTOR_DETACHED_TOKEN"] = instance
    from deeptutor.runtime import launcher

    def stop_watcher() -> None:
        while not stop_file.exists():
            time.sleep(0.2)
        paths = launcher._detached_launcher_paths(home)
        paths.stop.parent.mkdir(parents=True, exist_ok=True)
        paths.stop.write_text(instance, encoding="utf-8")

    threading.Thread(target=stop_watcher, daemon=True).start()
    try:
        launcher.start(
            home=home, open_browser=False, bind_host="127.0.0.1",
            on_ready=lambda endpoints: atomic_json(
                ready_file, {**endpoints, "instance": instance, "pid": os.getpid()}
            ),
        )
    finally:
        ready_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["serve", "workspaces", "add-workspace"])
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--instance")
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--folder")
    args = parser.parse_args()
    home = args.home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    with redirect_stdout(sys.stderr):
        initialize(home)
        if args.action == "serve":
            if not args.control or not args.instance:
                parser.error("serve requires --control and --instance")
            serve(home, args.control.resolve(), args.instance, args.gate)
            return
        result = workspace_action(args.action, args.folder)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
