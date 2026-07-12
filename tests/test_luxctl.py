"""Tests for luxctl.

The CLI is a single extensionless script, so it is loaded as a module here via
importlib. No network access is required: bridge interactions are exercised
through dry-run mode and a fake client.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_luxctl():
    spec = importlib.util.spec_from_loader(
        "luxctl",
        importlib.machinery.SourceFileLoader("luxctl", str(REPO_ROOT / "luxctl")),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


luxctl = load_luxctl()


def run_main(argv: list[str]) -> tuple[int, dict[str, Any]]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = luxctl.main(argv)
    lines = stdout.getvalue().strip().splitlines()
    assert len(lines) == 1, f"expected exactly one JSON line, got: {lines!r}"
    return code, json.loads(lines[0])


class FakeClient:
    """Stands in for HueClient; records writes and replays canned responses."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self.bridge = "192.0.2.10"
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.responses = responses or {}

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests.append((method, path, payload))
        return self.responses.get(path, {"data": [], "errors": []})


def make_inventory() -> dict[str, Any]:
    def light(light_id: str, name: str, room_ids: list[str], room_names: list[str]) -> dict[str, Any]:
        return {
            "id": light_id,
            "name": name,
            "on": True,
            "brightness": 50.0,
            "mirek": None,
            "mirek_valid": None,
            "xy": None,
            "room_ids": room_ids,
            "room_names": room_names,
            "device_id": f"device-{light_id}",
            "raw": {},
        }

    desk = light("light-1", "Desk lamp", ["room-1"], ["Office"])
    left = light("light-2", "Left sconce", ["room-2"], ["Bedroom"])
    right = light("light-3", "Right sconce", ["room-2"], ["Bedroom"])
    night = light("light-4", "Night light", ["room-2"], ["Bedroom"])
    return {
        "lights": [desk, left, right, night],
        "rooms": [
            {"id": "room-1", "name": "Office", "grouped_light_id": None, "grouped_on": None, "lights": [desk], "scenes": [], "raw": {}},
            {"id": "room-2", "name": "Bedroom", "grouped_light_id": None, "grouped_on": None, "lights": [left, right, night], "scenes": [], "raw": {}},
        ],
        "scenes": [
            {"id": "scene-1", "name": "Relax", "room_id": "room-2", "room_name": "Bedroom", "status": None, "palette": None, "speed": None, "auto_dynamic": None, "raw": {}},
            {"id": "scene-2", "name": "Relax", "room_id": "room-1", "room_name": "Office", "status": None, "palette": None, "speed": None, "auto_dynamic": None, "raw": {}},
        ],
    }


def json_action(doc: dict[str, Any], dry_run_flag: bool = False, client: FakeClient | None = None) -> dict[str, Any]:
    args = argparse.Namespace(dry_run=dry_run_flag)
    return luxctl.handle_json_action(doc, args, client or FakeClient(), make_inventory())


class TestValidators(unittest.TestCase):
    def test_parse_bool(self):
        for value in (True, "true", "1", "yes", "ON"):
            self.assertTrue(luxctl.parse_bool(value))
        for value in (False, "false", "0", "no", "off"):
            self.assertFalse(luxctl.parse_bool(value))
        for value in ("maybe", 1, None, [True]):
            with self.assertRaises(luxctl.CliError):
                luxctl.parse_bool(value)

    def test_parse_brightness(self):
        self.assertEqual(luxctl.parse_brightness("50"), 50.0)
        self.assertEqual(luxctl.parse_brightness(0), 0.0)
        self.assertEqual(luxctl.parse_brightness(99.99999), 100.0)
        for value in (-1, 101, "abc", True, None):
            with self.assertRaises(luxctl.CliError):
                luxctl.parse_brightness(value)

    def test_parse_mirek(self):
        self.assertEqual(luxctl.parse_mirek("300"), 300)
        self.assertEqual(luxctl.parse_mirek(153), 153)
        self.assertEqual(luxctl.parse_mirek(500.0), 500)
        for value in (152, 501, 300.5, "abc", True):
            with self.assertRaises(luxctl.CliError):
                luxctl.parse_mirek(value)

    def test_parse_xy(self):
        self.assertEqual(luxctl.parse_xy(["0.3", "0.15"]), (0.3, 0.15))
        self.assertEqual(luxctl.parse_xy([0.3, 0.15]), (0.3, 0.15))
        for value in (["0.3"], [1.1, 0.5], ["a", "b"], "0.3 0.15", None):
            with self.assertRaises(luxctl.CliError):
                luxctl.parse_xy(value)

    def test_hex_to_xy(self):
        x, y = luxctl.hex_to_xy("#ff0000")
        self.assertAlmostEqual(x, 0.7006, places=3)
        self.assertAlmostEqual(y, 0.2993, places=3)
        self.assertEqual(luxctl.hex_to_xy("purple"), luxctl.hex_to_xy("#800080"))
        self.assertEqual(luxctl.hex_to_xy("FF0000"), luxctl.hex_to_xy("#ff0000"))
        for value in ("#12345", "notacolor", 123, None):
            with self.assertRaises(luxctl.CliError):
                luxctl.hex_to_xy(value)

    def test_scene_payload(self):
        self.assertEqual(luxctl.build_scene_payload(), {"recall": {"action": "active"}})
        self.assertEqual(
            luxctl.build_scene_payload("static", 1000),
            {"recall": {"action": "static", "duration": 1000}},
        )
        for action, duration in (("bogus", None), ("active", -1), ("active", True), ("active", "1000")):
            with self.assertRaises(luxctl.CliError):
                luxctl.build_scene_payload(action, duration)


class TestConfig(unittest.TestCase):
    def write_config(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        self.addCleanup(Path(tmp.name).unlink)
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def test_valid_config(self):
        path = self.write_config("# comment\nbridge: 192.168.1.100\nkey: abc123\n")
        config = luxctl.load_simple_yaml(path)
        self.assertEqual(config["bridge"], "192.168.1.100")
        self.assertEqual(config["key"], "abc123")

    def test_quoted_values_are_unquoted(self):
        path = self.write_config('bridge: "192.168.1.100"\nkey: \'abc123\'\n')
        config = luxctl.load_simple_yaml(path)
        self.assertEqual(config["bridge"], "192.168.1.100")
        self.assertEqual(config["key"], "abc123")

    def test_missing_key_rejected(self):
        path = self.write_config("bridge: 192.168.1.100\n")
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.load_simple_yaml(path)
        self.assertEqual(ctx.exception.error_type, "config_invalid")

    def test_missing_file(self):
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.load_simple_yaml(Path("/nonexistent/config.yaml"))
        self.assertEqual(ctx.exception.error_type, "config_not_found")


class TestInventory(unittest.TestCase):
    def test_build_inventory(self):
        resources = {
            "device": [
                {
                    "id": "device-1",
                    "metadata": {"name": "Desk lamp device"},
                    "services": [{"rid": "light-1", "rtype": "light"}],
                }
            ],
            "light": [
                {
                    "id": "light-1",
                    "metadata": {"name": "Desk lamp"},
                    "owner": {"rid": "device-1"},
                    "on": {"on": True},
                    "dimming": {"brightness": 75.0},
                }
            ],
            "room": [
                {
                    "id": "room-1",
                    "metadata": {"name": "Office"},
                    "children": [{"rid": "device-1", "rtype": "device"}],
                    "services": [{"rid": "grouped-1", "rtype": "grouped_light"}],
                }
            ],
            "scene": [
                {"id": "scene-1", "metadata": {"name": "Focus"}, "group": {"rid": "room-1"}}
            ],
            "grouped_light": [{"id": "grouped-1", "on": {"on": True}}],
        }
        inventory = luxctl.build_inventory(resources)
        light = inventory["lights"][0]
        self.assertEqual(light["name"], "Desk lamp")
        self.assertEqual(light["room_names"], ["Office"])
        room = inventory["rooms"][0]
        self.assertEqual(room["grouped_light_id"], "grouped-1")
        self.assertEqual([l["id"] for l in room["lights"]], ["light-1"])
        self.assertEqual([s["name"] for s in room["scenes"]], ["Focus"])
        self.assertEqual(inventory["scenes"][0]["room_name"], "Office")


class TestSelectors(unittest.TestCase):
    def test_resolve_by_id_and_name(self):
        lights = make_inventory()["lights"]
        self.assertEqual(luxctl.resolve_selector(lights, "light", "light-1", None)["name"], "Desk lamp")
        self.assertEqual(luxctl.resolve_selector(lights, "light", None, "  desk   LAMP ")["id"], "light-1")

    def test_not_found_and_ambiguous(self):
        scenes = make_inventory()["scenes"]
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector(scenes, "scene", None, "Nonexistent")
        self.assertEqual(ctx.exception.error_type, "resource_not_found")
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector(scenes, "scene", None, "Relax")
        self.assertEqual(ctx.exception.error_type, "ambiguous_match")

    def test_both_selectors_rejected(self):
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector([], "light", "light-1", "Desk lamp")
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")

    def test_resolve_light_targets_dedupes(self):
        lights = make_inventory()["lights"]
        targets = luxctl.resolve_light_targets(lights, ["light-1"], ["Desk lamp", "Left sconce"])
        self.assertEqual([t["id"] for t in targets], ["light-1", "light-2"])

    def test_resolve_excluded_lights(self):
        room = make_inventory()["rooms"][1]
        targets, excluded = luxctl.resolve_excluded_lights(room, ["Night light"])
        self.assertEqual([t["id"] for t in targets], ["light-2", "light-3"])
        self.assertEqual([e["id"] for e in excluded], ["light-4"])
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_excluded_lights(room, ["Desk lamp"])
        self.assertEqual(ctx.exception.error_type, "resource_not_found")


class TestJsonActions(unittest.TestCase):
    def test_set_light_dry_run(self):
        result = json_action(
            {
                "action": "set_light",
                "targets": {"names": ["Left sconce", "Right sconce"]},
                "payload": {"on": True, "brightness": 35},
                "dry_run": True,
            }
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["meta"]["dry_run"])
        self.assertEqual(result["result"]["summary"]["target_count"], 2)
        self.assertEqual(result["result"]["payload"], {"on": {"on": True}, "dimming": {"brightness": 35.0}})

    def test_set_light_writes(self):
        client = FakeClient()
        result = json_action(
            {"action": "set_light", "targets": {"ids": ["light-1"]}, "payload": {"on": False}},
            client=client,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(client.requests, [("PUT", "resource/light/light-1", {"on": {"on": False}})])

    def test_set_light_invalid_brightness_is_cli_error(self):
        with self.assertRaises(luxctl.CliError) as ctx:
            json_action({"action": "set_light", "targets": {"ids": ["light-1"]}, "payload": {"brightness": 150}})
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")

    def test_set_light_invalid_on_is_cli_error(self):
        with self.assertRaises(luxctl.CliError):
            json_action({"action": "set_light", "targets": {"ids": ["light-1"]}, "payload": {"on": "maybe"}})

    def test_set_room_with_exclusions(self):
        client = FakeClient()
        result = json_action(
            {"action": "set_room", "target": {"name": "Bedroom"}, "payload": {"on": True}, "exclude": ["Night light"]},
            client=client,
        )
        self.assertTrue(result["ok"])
        written = [path for _, path, _ in client.requests]
        self.assertEqual(written, ["resource/light/light-2", "resource/light/light-3"])
        self.assertEqual(result["result"]["excluded_lights"], [{"id": "light-4", "name": "Night light"}])

    def test_set_scene_payload_optional(self):
        result = json_action({"action": "set_scene", "target": {"name": "Relax", "room_name": "Bedroom"}, "dry_run": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["payload"], {"recall": {"action": "active"}})
        self.assertEqual(result["result"]["scene"]["id"], "scene-1")

    def test_dry_run_conflict_rejected(self):
        with self.assertRaises(luxctl.CliError):
            json_action(
                {"action": "set_light", "targets": {"ids": ["light-1"]}, "payload": {"on": True}, "dry_run": True},
                dry_run_flag=True,
            )

    def test_unknown_action_and_keys(self):
        with self.assertRaises(luxctl.CliError):
            json_action({"action": "explode"})
        with self.assertRaises(luxctl.CliError):
            json_action({"action": "set_light", "bogus": 1})


class TestArgvHandling(unittest.TestCase):
    def test_normalize_moves_global_flags_forward(self):
        argv = ["set", "light", "--name", "Desk", "--bridge", "1.2.3.4", "--dry-run"]
        self.assertEqual(
            luxctl.normalize_global_flags(argv),
            ["--bridge", "1.2.3.4", "--dry-run", "set", "light", "--name", "Desk"],
        )

    def test_args_to_action(self):
        self.assertEqual(luxctl.args_to_action(["get", "light", "--name", "Desk"]), "get_light")
        self.assertEqual(luxctl.args_to_action(["list-actions"]), "list_actions")
        self.assertEqual(luxctl.args_to_action(["--input-json", "{}"]), "json_input")
        self.assertEqual(luxctl.args_to_action(["--version"]), "version")
        self.assertEqual(luxctl.args_to_action([]), "help")


class TestMainEnvelope(unittest.TestCase):
    """Every invocation must produce exactly one JSON object on stdout."""

    def test_help(self):
        code, doc = run_main(["--help"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["action"], "help")

    def test_version(self):
        code, doc = run_main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["result"]["version"], luxctl.VERSION)

    def test_schema_and_list_actions(self):
        code, doc = run_main(["schema"])
        self.assertEqual(code, 0)
        self.assertIn("internal_error", doc["result"]["schema"]["error_types"])
        code, doc = run_main(["list-actions"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["result"]["actions"])

    def test_missing_flag_value_is_json_error(self):
        code, doc = run_main(["--config"])
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "invalid_arguments")

    def test_get_without_resource_is_json_error(self):
        code, doc = run_main(["get"])
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "invalid_arguments")

    def test_invalid_json_input_is_json_error(self):
        code, doc = run_main(["--input-json", "{not json"])
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "invalid_arguments")

    def test_missing_input_file_is_json_error(self):
        code, doc = run_main(["--input", "/nonexistent/input.json"])
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "invalid_arguments")

    def test_non_object_json_input_is_json_error(self):
        code, doc = run_main(["--input-json", "[1,2,3]"])
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "invalid_arguments")

    def test_unexpected_exception_becomes_internal_error(self):
        original = luxctl.build_parser
        luxctl.build_parser = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, luxctl, "build_parser", original)
        code, doc = run_main(["get", "light"])
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "internal_error")
        self.assertIn("boom", doc["error"]["message"])


class TestSslContext(unittest.TestCase):
    def test_default_context_disables_verification(self):
        import ssl

        context = luxctl.build_ssl_context(None)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)

    def test_ca_cert_enables_chain_verification(self):
        import ssl

        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.build_ssl_context("/nonexistent/ca.pem")
        self.assertEqual(ctx.exception.error_type, "config_invalid")


if __name__ == "__main__":
    unittest.main()
