from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch


RUNNER = Path(__file__).resolve().parents[1] / "runtime/runner.py"
spec = importlib.util.spec_from_file_location("desktop_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@contextmanager
def fake_modules(**items):
    modules = {name: types.ModuleType(name) for name in ["deeptutor", "deeptutor.services", "deeptutor.services.config", "deeptutor.services.workspace", "deeptutor.services.workspace.service", "deeptutor.runtime"]}
    for name, attributes in items.items():
        modules[name.replace("__", ".")].__dict__.update(attributes)
    with patch.dict(sys.modules, modules):
        yield


class RunnerTests(unittest.TestCase):
    def test_bundled_agent_clis_are_found_without_global_path_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "Local"
            codex = local / "OpenAI/Codex/bin/current/codex.exe"
            dsh = root / "DeepSeek/resources/runtime/cli/bin/dsh.cmd"
            for file in (codex, dsh):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            with patch.object(runner.sys, "platform", "win32"), patch.object(runner.shutil, "which", return_value=None), patch.object(runner, "_installed_app_roots", return_value=[]), patch.dict(os.environ, {"LOCALAPPDATA": str(local), "PATH": "original", "DEEPTUTOR_DSH_CLI_DIR": ""}):
                runner.extend_agent_cli_path(root / "learning")
                self.assertEqual(os.environ["PATH"].split(os.pathsep), ["original", str(codex.parent), str(dsh.parent)])

    def test_existing_agent_commands_keep_precedence(self):
        with patch.object(runner.sys, "platform", "win32"), patch.object(runner.shutil, "which", return_value="existing"), patch.dict(os.environ, {"PATH": "original"}):
            runner.extend_agent_cli_path(Path(tempfile.gettempdir()) / "learning")
            self.assertEqual(os.environ["PATH"], "original")

    def test_control_protocol_uses_utf8_on_non_utf8_windows_consoles(self):
        with tempfile.TemporaryDirectory() as home:
            data = io.BytesIO()
            console = io.TextIOWrapper(data, encoding="cp1252")
            with patch.object(sys, "argv", ["runner", "workspaces", "--home", home]), patch.object(sys, "stdout", console), patch.object(sys, "stderr", io.StringIO()), patch.object(runner, "initialize"), patch.object(runner, "workspace_action", return_value={"name": "学习资料"}):
                runner.main()
                console.flush()
                self.assertEqual(json.loads(data.getvalue().decode("utf-8")), {"name": "学习资料"})

    def test_ready_file_is_atomic_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "control/instance.json"
            runner.atomic_json(target, {"home": "学习资料"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"home": "学习资料"})
            self.assertFalse(target.with_suffix(".tmp").exists())

    def test_workspace_controls_cannot_bypass_multi_user_login(self):
        class ForbiddenService:
            def __init__(self):
                raise AssertionError("Registry must not be accessed before login")
        with fake_modules(
            deeptutor__services__config={"load_auth_settings": lambda: {"enabled": True}},
            deeptutor__services__workspace__service={"ContentWorkspaceService": ForbiddenService},
        ):
            with self.assertRaisesRegex(RuntimeError, "多用户登录"):
                runner.workspace_action("workspaces")

    def test_serve_binds_loopback_waits_for_readiness_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder:
            home, control = Path(folder) / "home", Path(folder) / "control"
            home.mkdir()
            control.mkdir()
            gate = control / "gate"
            gate.touch()
            stop_path = home / "launcher.stop"

            def start(**kwargs):
                self.assertEqual(kwargs["bind_host"], "127.0.0.1")
                self.assertFalse(kwargs["open_browser"])
                self.assertTrue(kwargs["auto_ports"])
                self.assertFalse((control / "test.json").exists())
                kwargs["on_ready"]({"frontend_url": "http://127.0.0.1:1234"})
                self.assertEqual(json.loads((control / "test.json").read_text())["instance"], "test")
                (control / "test.stop").touch()
                deadline = time.monotonic() + 3
                while not stop_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(stop_path.read_text(), "test")

            launcher = types.SimpleNamespace(start=start, _detached_launcher_paths=lambda _: types.SimpleNamespace(stop=stop_path))
            with fake_modules(deeptutor__runtime={"launcher": launcher}), patch.dict("os.environ", {}):
                runner.serve(home, control, "test", gate)
            self.assertFalse(gate.exists())
            self.assertFalse((control / "test.json").exists())
            self.assertFalse((control / "test.stop").exists())


if __name__ == "__main__":
    unittest.main()
