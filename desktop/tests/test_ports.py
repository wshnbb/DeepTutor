"""Real socket regressions without importing optional model dependencies."""
import ast
from pathlib import Path
import socket
import unittest
from unittest.mock import Mock


source = Path(__file__).resolve().parents[2] / "deeptutor/runtime/launcher.py"
nodes = [node for node in ast.parse(source.read_text(encoding="utf-8")).body
         if isinstance(node, ast.FunctionDef) and node.name in {
             "_select_loopback_ports", "_resolve_port_conflicts"}]


class AutomaticPortsTests(unittest.TestCase):
    def setUp(self):
        self.scope = {"socket": socket, "Path": Path, "_persist_ports": Mock(),
                      "_log": Mock(), "_t": Mock(return_value="saved")}
        for name in ["_prompt_conflict_choice", "_prompt_new_ports", "_kill_port_listeners",
                     "_port_listeners", "_owned_orphan_started_at"]:
            self.scope[name] = Mock(side_effect=AssertionError(f"unexpected {name}"))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.scope)

    def test_occupied_ports_choose_distinct_ports_without_touching_the_listeners(self):
        with socket.socket() as backend, socket.socket() as frontend:
            backend.bind(("127.0.0.1", 0)); backend.listen()
            frontend.bind(("127.0.0.1", 0)); frontend.listen()
            old = backend.getsockname()[1], frontend.getsockname()[1]
            selected = self.scope["_resolve_port_conflicts"](
                backend_port=old[0], frontend_port=old[1], check_frontend=True,
                settings_dir=Path("settings"), auto_ports=True)
            self.assertEqual(len(set(selected)), 2)
            self.assertTrue(set(old).isdisjoint(selected))
            self.scope["_persist_ports"].assert_called_once_with(Path("settings"), *selected)
            for listener in [backend, frontend]:
                with socket.create_connection(listener.getsockname(), timeout=1):
                    pass

    def test_same_requested_port_gets_two_different_bindable_ports(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            preferred = probe.getsockname()[1]
        selected = self.scope["_select_loopback_ports"](preferred, preferred, True)
        self.assertEqual(selected[0], preferred)
        self.assertNotEqual(*selected)

    def test_available_saved_ports_are_not_rewritten(self):
        with socket.socket() as first, socket.socket() as second:
            first.bind(("127.0.0.1", 0)); second.bind(("127.0.0.1", 0))
            preferred = first.getsockname()[1], second.getsockname()[1]
        self.assertEqual(self.scope["_resolve_port_conflicts"](
            backend_port=preferred[0], frontend_port=preferred[1], check_frontend=True,
            settings_dir=Path("settings"), auto_ports=True), preferred)
        self.scope["_persist_ports"].assert_not_called()
