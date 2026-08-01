"""Tests for luxctl.

The CLI is a single extensionless script, so it is loaded as a module here via
importlib. No network access is required: bridge interactions are exercised
through dry-run mode and a fake client.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
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


def parse(argv: list[str]) -> argparse.Namespace:
    """Parse through the real argv pipeline so flag wiring is covered too."""
    return luxctl.build_parser().parse_args(luxctl.normalize_global_flags(argv))


class FakeClient:
    """Stands in for HueClient; records writes and replays canned responses."""

    def __init__(self, responses: dict[str, Any] | None = None, failing: set[str] | None = None):
        self.bridge = "192.0.2.10"
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.responses = responses or {}
        self.failing = failing or set()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests.append((method, path, payload))
        if path in self.failing:
            raise luxctl.CliError("api_rejected_request", "Hue bridge rejected the request", {"status": 500})
        return self.responses.get(path, {"data": [], "errors": []})


def raw_resources() -> dict[str, list[dict[str, Any]]]:
    """A bridge fixture with deliberate name collisions across rooms."""

    def device(device_id: str, light_id: str, name: str) -> dict[str, Any]:
        return {
            "id": device_id,
            "metadata": {"name": name},
            "services": [{"rid": light_id, "rtype": "light"}],
        }

    def light(light_id: str, device_id: str, name: str, on: bool, brightness: float = 75.0) -> dict[str, Any]:
        return {
            "id": light_id,
            "metadata": {"name": name},
            "owner": {"rid": device_id},
            "on": {"on": on},
            "dimming": {"brightness": brightness},
            "color_temperature": {"mirek": 366, "mirek_valid": True},
            "effects": {"status_values": ["no_effect", "candle"], "status": "no_effect"},
        }

    specs = [
        ("light-1", "device-1", "Desk lamp", True, "room-1"),
        ("light-2", "device-2", "Lamp", True, "room-1"),
        ("light-3", "device-3", "Left sconce", True, "room-2"),
        ("light-4", "device-4", "Right sconce", False, "room-2"),
        ("light-5", "device-5", "Night light", True, "room-2"),
        ("light-6", "device-6", "Lamp", False, "room-2"),
    ]
    lights = [light(lid, did, name, on) for lid, did, name, on, _ in specs]
    devices = [device(did, lid, name) for lid, did, name, _, _ in specs]
    rooms = [
        {
            "id": room_id,
            "metadata": {"name": room_name},
            "children": [{"rid": did, "rtype": "device"} for _, did, _, _, rid in specs if rid == room_id],
            "services": [{"rid": f"grouped-{room_id}", "rtype": "grouped_light"}],
        }
        for room_id, room_name in (("room-1", "Office"), ("room-2", "Bedroom"))
    ]
    return {
        "device": devices,
        "light": lights,
        "room": rooms,
        "scene": [
            {"id": "scene-1", "metadata": {"name": "Relax"}, "group": {"rid": "room-2"}},
            {"id": "scene-2", "metadata": {"name": "Relax"}, "group": {"rid": "room-1"}},
            {"id": "scene-3", "metadata": {"name": "Focus"}, "group": {"rid": "room-1"}},
        ],
        "grouped_light": [
            {"id": "grouped-room-1", "on": {"on": True}},
            {"id": "grouped-room-2", "on": {"on": False}},
        ],
    }


def make_inventory() -> dict[str, Any]:
    return luxctl.build_inventory(raw_resources())


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

    def test_parse_int_helpers(self):
        self.assertEqual(luxctl.parse_non_negative_int("0"), 0)
        self.assertEqual(luxctl.parse_positive_int("5"), 5)
        for value in ("-1", "1.5", "abc"):
            with self.assertRaises(luxctl.CliError):
                luxctl.parse_non_negative_int(value)
        with self.assertRaises(luxctl.CliError):
            luxctl.parse_positive_int("0")

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


class TestRefs(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(luxctl.slugify("Kitchen Ceiling"), "kitchen-ceiling")
        self.assertEqual(luxctl.slugify("  Desk   Lamp!  "), "desk-lamp")
        self.assertEqual(luxctl.slugify("!!!"), "unnamed")

    def test_unique_names_get_bare_refs(self):
        inventory = make_inventory()
        refs = {light["ref"] for light in inventory["lights"]}
        self.assertIn("desk-lamp", refs)
        self.assertIn("night-light", refs)

    def test_colliding_names_are_room_qualified(self):
        inventory = make_inventory()
        by_id = {light["id"]: light["ref"] for light in inventory["lights"]}
        self.assertEqual(by_id["light-2"], "office/lamp")
        self.assertEqual(by_id["light-6"], "bedroom/lamp")

    def test_colliding_scene_names_are_room_qualified(self):
        inventory = make_inventory()
        by_id = {scene["id"]: scene["ref"] for scene in inventory["scenes"]}
        self.assertEqual(by_id["scene-1"], "bedroom/relax")
        self.assertEqual(by_id["scene-2"], "office/relax")
        self.assertEqual(by_id["scene-3"], "focus")

    def test_duplicate_names_in_one_room_get_stable_suffixes(self):
        items = [{"id": "b", "name": "Lamp"}, {"id": "a", "name": "Lamp"}]
        refs = luxctl.assign_refs(items, qualifier=lambda item: "Kitchen")
        # Suffixes follow uuid order, so they do not shuffle between calls.
        self.assertEqual(refs["a"], "kitchen/lamp")
        self.assertEqual(refs["b"], "kitchen/lamp-2")

    def test_refs_resolve_as_selectors(self):
        inventory = make_inventory()
        light = luxctl.resolve_selector(inventory["lights"], "light", item_ref="bedroom/lamp")
        self.assertEqual(light["id"], "light-6")

    def test_unknown_ref_reports_not_found_with_hint(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector(inventory["lights"], "light", item_ref="no-such-light")
        self.assertEqual(ctx.exception.error_type, "resource_not_found")
        self.assertIn("hint", ctx.exception.details)


class TestInventory(unittest.TestCase):
    def test_build_inventory(self):
        inventory = make_inventory()
        light = next(item for item in inventory["lights"] if item["id"] == "light-1")
        self.assertEqual(light["name"], "Desk lamp")
        self.assertEqual(light["ref"], "desk-lamp")
        self.assertEqual(light["room_names"], ["Office"])
        self.assertEqual(light["room_refs"], ["office"])

        room = next(item for item in inventory["rooms"] if item["id"] == "room-2")
        self.assertEqual(room["ref"], "bedroom")
        self.assertFalse(room["grouped_on"])
        self.assertEqual(len(room["lights"]), 4)

    def test_missing_resource_types_are_tolerated(self):
        resources = dict(raw_resources())
        resources["scene"] = []
        resources["grouped_light"] = []
        inventory = luxctl.build_inventory(resources)
        self.assertEqual(inventory["scenes"], [])
        self.assertIsNone(inventory["rooms"][0]["grouped_on"])


class TestViews(unittest.TestCase):
    def test_compact_light_excludes_raw_and_uuid(self):
        record = make_inventory()["lights"][0]
        view = luxctl.view_light(record)
        self.assertEqual(set(view), {"ref", "name", "on", "brightness", "room"})
        self.assertNotIn("raw", view)
        self.assertNotIn("uuid", view)

    def test_detail_adds_uuid_and_colour(self):
        record = make_inventory()["lights"][0]
        view = luxctl.view_light(record, detail=True)
        self.assertEqual(view["uuid"], "light-1")
        self.assertEqual(view["mirek"], 366)
        self.assertNotIn("raw", view)

    def test_raw_adds_bridge_resource(self):
        record = make_inventory()["lights"][0]
        view = luxctl.view_light(record, raw=True)
        self.assertIn("effects", view["raw"])

    def test_compact_room_omits_members(self):
        record = make_inventory()["rooms"][1]
        view = luxctl.view_room(record)
        self.assertEqual(set(view), {"ref", "name", "on", "light_count"})
        self.assertEqual(view["light_count"], 4)

    def test_room_expansions_are_opt_in(self):
        record = make_inventory()["rooms"][1]
        view = luxctl.view_room(record, with_lights=True, with_scenes=True)
        self.assertEqual(len(view["lights"]), 4)
        self.assertEqual(len(view["scenes"]), 1)
        self.assertNotIn("raw", view["lights"][0])

    def test_compact_scene_is_three_fields(self):
        record = make_inventory()["scenes"][0]
        self.assertEqual(set(luxctl.view_scene(record)), {"ref", "name", "room"})

    def test_brightness_is_rounded_in_compact_view(self):
        record = make_inventory()["lights"][0]
        record["brightness"] = 62.45
        self.assertEqual(luxctl.view_light(record)["brightness"], 62)
        self.assertEqual(luxctl.view_light(record, detail=True)["brightness_exact"], 62.45)


class TestBudget(unittest.TestCase):
    def test_untruncated_list_still_reports_counts(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "light", "--all"]), inventory, FakeClient())
        self.assertEqual(doc["result"]["count"], 6)
        self.assertEqual(doc["result"]["total"], 6)
        self.assertFalse(doc["result"]["truncated"])

    def test_budget_trims_and_announces(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["--max-bytes", "600", "get", "light", "--all"]), inventory, FakeClient())
        result = doc["result"]
        self.assertTrue(result["truncated"])
        self.assertLess(result["count"], result["total"])
        self.assertEqual(result["total"], 6)
        self.assertIn("hint", result)
        self.assertLessEqual(len(luxctl.encode(doc)), 600)

    def test_notice_survives_an_unmeetable_budget(self):
        # The truncation notice is never dropped to hit a budget: a caller that
        # asks for 50 bytes still learns the list is incomplete.
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["--max-bytes", "50", "get", "light", "--all"]), inventory, FakeClient())
        self.assertTrue(doc["result"]["truncated"])
        self.assertEqual(doc["result"]["count"], 0)
        self.assertEqual(doc["result"]["total"], 6)
        self.assertIn("hint", doc["result"])

    def test_zero_budget_disables_trimming(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["--max-bytes", "0", "get", "light", "--all"]), inventory, FakeClient())
        self.assertFalse(doc["result"]["truncated"])
        self.assertEqual(doc["result"]["count"], 6)

    def test_budget_leaves_non_list_results_alone(self):
        payload = luxctl.success("get_light", {"light": {"ref": "x" * 500}}, {})
        self.assertEqual(luxctl.enforce_budget(payload, "lights", 50), payload)

    def test_clamp_detail_caps_oversized_payloads(self):
        clamped = luxctl.clamp_detail("x" * (luxctl.MAX_ERROR_DETAIL_CHARS + 100))
        self.assertTrue(clamped["truncated"])
        self.assertEqual(len(clamped["preview"]), luxctl.MAX_ERROR_DETAIL_CHARS)
        self.assertEqual(luxctl.clamp_detail("short"), "short")


class TestGetGating(unittest.TestCase):
    def test_listing_requires_all(self):
        inventory = make_inventory()
        for resource in ("light", "room", "scene"):
            with self.assertRaises(luxctl.CliError) as ctx:
                luxctl.handle_get(parse(["get", resource]), inventory, FakeClient())
            self.assertEqual(ctx.exception.error_type, "selector_required")

    def test_filter_counts_as_intent(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "light", "--on", "true"]), inventory, FakeClient())
        self.assertEqual(doc["result"]["count"], 4)
        self.assertEqual(doc["result"]["total"], 4)
        self.assertTrue(all(light["on"] for light in doc["result"]["lights"]))

    def test_off_filter(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "light", "--on", "false"]), inventory, FakeClient())
        self.assertEqual({light["ref"] for light in doc["result"]["lights"]}, {"right-sconce", "bedroom/lamp"})

    def test_room_filter(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "light", "--room", "office"]), inventory, FakeClient())
        self.assertEqual(doc["result"]["count"], 2)

    def test_room_filter_accepts_name_and_uuid(self):
        inventory = make_inventory()
        for value in ("Office", "room-1", "office"):
            doc = luxctl.handle_get(parse(["get", "light", "--room", value]), inventory, FakeClient())
            self.assertEqual(doc["result"]["count"], 2)

    def test_scene_room_filter_counts_as_intent(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "scene", "--room", "office"]), inventory, FakeClient())
        self.assertEqual({scene["ref"] for scene in doc["result"]["scenes"]}, {"office/relax", "focus"})

    def test_limit_keeps_total(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "light", "--all", "--limit", "2"]), inventory, FakeClient())
        self.assertEqual(doc["result"]["count"], 2)
        self.assertEqual(doc["result"]["total"], 6)

    def test_count_only(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "light", "--all", "--count"]), inventory, FakeClient())
        self.assertEqual(doc["result"], {"count": 6, "total": 6})

    def test_raw_requires_selector(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.handle_get(parse(["--raw", "get", "light", "--all"]), inventory, FakeClient())
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")

    def test_raw_with_selector_is_allowed(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["--raw", "get", "light", "--ref", "desk-lamp"]), inventory, FakeClient())
        self.assertIn("raw", doc["result"]["light"])
        self.assertEqual(doc["meta"]["view"], "raw")

    def test_single_room_expansion(self):
        inventory = make_inventory()
        doc = luxctl.handle_get(parse(["get", "room", "--ref", "bedroom", "--with-lights"]), inventory, FakeClient())
        self.assertEqual(len(doc["result"]["room"]["lights"]), 4)


class TestSelectors(unittest.TestCase):
    def test_resolve_by_id_name_and_ref(self):
        inventory = make_inventory()
        self.assertEqual(luxctl.resolve_selector(inventory["lights"], "light", item_id="light-1")["ref"], "desk-lamp")
        self.assertEqual(luxctl.resolve_selector(inventory["lights"], "light", item_name="Desk lamp")["id"], "light-1")
        self.assertEqual(luxctl.resolve_selector(inventory["lights"], "light", item_ref="desk-lamp")["id"], "light-1")

    def test_ambiguous_name_suggests_ref(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector(inventory["lights"], "light", item_name="Lamp")
        self.assertEqual(ctx.exception.error_type, "ambiguous_match")
        self.assertEqual(
            {match["ref"] for match in ctx.exception.details["matches"]}, {"office/lamp", "bedroom/lamp"}
        )

    def test_multiple_selectors_rejected(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector(inventory["lights"], "light", item_id="light-1", item_ref="desk-lamp")
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")

    def test_no_selector_is_selector_required(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_selector(inventory["lights"], "light")
        self.assertEqual(ctx.exception.error_type, "selector_required")

    def test_resolve_light_targets_dedupes(self):
        inventory = make_inventory()
        targets = luxctl.resolve_light_targets(
            inventory["lights"], ids=["light-1"], names=["Desk lamp"], refs=["desk-lamp"]
        )
        self.assertEqual([target["id"] for target in targets], ["light-1"])

    def test_resolve_light_targets_all(self):
        inventory = make_inventory()
        targets = luxctl.resolve_light_targets(inventory["lights"], select_all=True)
        self.assertEqual(len(targets), 6)

    def test_all_conflicts_with_selectors(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_light_targets(inventory["lights"], refs=["desk-lamp"], select_all=True)
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")

    def test_no_selector_and_no_all_is_selector_required(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.resolve_light_targets(inventory["lights"])
        self.assertEqual(ctx.exception.error_type, "selector_required")

    def test_partition_excluded_by_ref_name_and_uuid(self):
        inventory = make_inventory()
        room = next(item for item in inventory["rooms"] if item["ref"] == "bedroom")
        for value in ("night-light", "Night light", "light-5"):
            targets, excluded = luxctl.partition_excluded(room["lights"], [value], "bedroom")
            self.assertEqual([light["id"] for light in excluded], ["light-5"])
            self.assertEqual(len(targets), 3)

    def test_partition_excluded_unknown_light(self):
        inventory = make_inventory()
        room = next(item for item in inventory["rooms"] if item["ref"] == "bedroom")
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.partition_excluded(room["lights"], ["nope"], "bedroom")
        self.assertEqual(ctx.exception.error_type, "resource_not_found")


class TestWrites(unittest.TestCase):
    def test_set_light_compact_response(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_light(parse(["set", "light", "--ref", "desk-lamp", "--on", "false"]), client, inventory)
        result = doc["result"]
        self.assertEqual(result["target"], {"ref": "desk-lamp", "name": "Desk lamp"})
        self.assertEqual(result["applied"], {"on": {"on": False}})
        self.assertEqual(result["summary"]["success_count"], 1)
        self.assertNotIn("writes", result)
        self.assertNotIn("raw", luxctl.encode(result))

    def test_verbose_adds_write_echoes(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_light(
            parse(["--verbose", "set", "light", "--ref", "desk-lamp", "--on", "false"]), client, inventory
        )
        self.assertEqual(len(doc["result"]["writes"]), 1)
        self.assertIn("bridge_response", doc["result"]["writes"][0])

    def test_set_light_all_targets_everything(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_light(parse(["set", "light", "--all", "--on", "false"]), client, inventory)
        self.assertEqual(doc["result"]["scope"], "all_lights")
        self.assertEqual(doc["result"]["summary"]["target_count"], 6)
        self.assertEqual(len(client.requests), 6)

    def test_failures_surface_without_verbose(self):
        inventory = make_inventory()
        client = FakeClient(failing={"resource/light/light-4"})
        doc = luxctl.handle_set_light(parse(["set", "light", "--all", "--on", "false"]), client, inventory)
        result = doc["result"]
        self.assertEqual(result["summary"]["failure_count"], 1)
        self.assertEqual(result["failures"][0]["target"]["ref"], "right-sconce")
        self.assertNotIn("writes", result)

    def test_set_room_reports_room_not_members(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_room(parse(["set", "room", "--ref", "bedroom", "--on", "false"]), client, inventory)
        result = doc["result"]
        self.assertEqual(result["room"], {"ref": "bedroom", "name": "Bedroom"})
        self.assertEqual(result["summary"]["target_count"], 4)
        self.assertNotIn("targets", result)
        self.assertNotIn("target_lights", result)

    def test_set_room_exclusions_are_listed(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_room(
            parse(["set", "room", "--ref", "bedroom", "--on", "false", "--exclude", "night-light"]), client, inventory
        )
        self.assertEqual(doc["result"]["excluded"], [{"ref": "night-light", "name": "Night light"}])
        self.assertEqual(doc["result"]["summary"]["target_count"], 3)

    def test_set_room_all_spans_every_room(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_room(parse(["set", "room", "--all", "--on", "false"]), client, inventory)
        self.assertEqual(doc["result"]["scope"], "all_rooms")
        self.assertEqual(doc["result"]["room_count"], 2)
        self.assertEqual(doc["result"]["summary"]["target_count"], 6)

    def test_set_room_all_with_exclusion_resolves_globally(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_room(
            parse(["set", "room", "--all", "--on", "false", "--exclude", "night-light"]), client, inventory
        )
        self.assertEqual(doc["result"]["summary"]["target_count"], 5)
        self.assertEqual(doc["result"]["excluded"], [{"ref": "night-light", "name": "Night light"}])

    def test_dry_run_sends_nothing(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_room(
            parse(["--dry-run", "set", "room", "--ref", "bedroom", "--on", "false"]), client, inventory
        )
        self.assertEqual(client.requests, [])
        self.assertTrue(doc["result"]["summary"]["dry_run"])
        self.assertEqual(doc["result"]["payload"], {"on": {"on": False}})

    def test_set_scene_returns_reference_only(self):
        inventory = make_inventory()
        client = FakeClient()
        doc = luxctl.handle_set_scene(parse(["set", "scene", "--ref", "bedroom/relax"]), client, inventory)
        self.assertEqual(doc["result"]["scene"], {"ref": "bedroom/relax", "name": "Relax", "room": "bedroom"})
        self.assertNotIn("raw", luxctl.encode(doc["result"]))
        self.assertNotIn("bridge_response", doc["result"])

    def test_set_scene_ambiguous_name_needs_room_or_ref(self):
        inventory = make_inventory()
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.handle_set_scene(parse(["set", "scene", "--name", "Relax"]), FakeClient(), inventory)
        self.assertEqual(ctx.exception.error_type, "ambiguous_match")
        doc = luxctl.handle_set_scene(
            parse(["set", "scene", "--name", "Relax", "--room", "office"]), FakeClient(), inventory
        )
        self.assertEqual(doc["result"]["scene"]["ref"], "office/relax")


class TestFetchPlanning(unittest.TestCase):
    def test_scenes_skipped_when_not_needed(self):
        self.assertEqual(luxctl.plan_fetch(parse(["get", "light", "--all"]), None), {"scenes": False, "grouped": False})
        self.assertEqual(luxctl.plan_fetch(parse(["set", "light", "--all", "--on", "false"]), None), {"scenes": False, "grouped": False})

    def test_scenes_fetched_when_needed(self):
        self.assertTrue(luxctl.plan_fetch(parse(["get", "scene", "--all"]), None)["scenes"])
        self.assertTrue(luxctl.plan_fetch(parse(["get", "room", "--all", "--with-scenes"]), None)["scenes"])
        self.assertTrue(luxctl.plan_fetch(parse(["--detail", "get", "room", "--all"]), None)["scenes"])
        self.assertTrue(luxctl.plan_fetch(argparse.Namespace(), {"action": "set_scene"})["scenes"])

    def test_grouped_fetched_only_for_room_reads(self):
        self.assertTrue(luxctl.plan_fetch(parse(["get", "room", "--all"]), None)["grouped"])
        self.assertFalse(luxctl.plan_fetch(parse(["set", "room", "--all", "--on", "false"]), None)["grouped"])

    def test_fetch_inventory_skips_endpoints(self):
        client = FakeClient()
        luxctl.fetch_inventory(client, scenes=False, grouped=False)
        paths = [path for _, path, _ in client.requests]
        self.assertNotIn("resource/scene", paths)
        self.assertNotIn("resource/grouped_light", paths)
        self.assertIn("resource/light", paths)


class TestJsonActions(unittest.TestCase):
    def run_action(self, doc: dict[str, Any], client: FakeClient | None = None, **flags: Any):
        client = client or FakeClient()
        args = argparse.Namespace(dry_run=flags.get("dry_run", False), verbose=flags.get("verbose", False))
        return luxctl.handle_json_action(doc, args, client, make_inventory()), client

    def test_set_light_by_ref(self):
        doc, client = self.run_action(
            {"action": "set_light", "targets": {"refs": ["bedroom/lamp"]}, "payload": {"on": True}}
        )
        self.assertEqual(doc["result"]["target"]["ref"], "bedroom/lamp")
        self.assertEqual(client.requests[0][1], "resource/light/light-6")

    def test_set_light_all(self):
        doc, client = self.run_action({"action": "set_light", "targets": {"all": True}, "payload": {"on": False}})
        self.assertEqual(doc["result"]["scope"], "all_lights")
        self.assertEqual(len(client.requests), 6)

    def test_set_light_dry_run(self):
        doc, client = self.run_action(
            {"action": "set_light", "targets": {"refs": ["desk-lamp"]}, "payload": {"on": True}, "dry_run": True}
        )
        self.assertEqual(client.requests, [])
        self.assertTrue(doc["meta"]["dry_run"])

    def test_set_room_all_with_exclusions(self):
        doc, client = self.run_action(
            {"action": "set_room", "target": {"all": True}, "payload": {"on": False}, "exclude": ["night-light"]}
        )
        self.assertEqual(doc["result"]["summary"]["target_count"], 5)

    def test_set_room_by_ref_with_exclusions(self):
        doc, client = self.run_action(
            {"action": "set_room", "target": {"ref": "bedroom"}, "payload": {"on": False}, "exclude": ["night-light"]}
        )
        self.assertEqual(doc["result"]["summary"]["target_count"], 3)
        self.assertEqual(doc["result"]["room"]["ref"], "bedroom")

    def test_set_scene_by_ref(self):
        doc, client = self.run_action({"action": "set_scene", "target": {"ref": "office/relax"}})
        self.assertEqual(doc["result"]["scene"]["ref"], "office/relax")
        self.assertEqual(client.requests[0][1], "resource/scene/scene-2")

    def test_invalid_payload_is_cli_error(self):
        for payload in ({"brightness": 150}, {"on": "maybe"}):
            with self.assertRaises(luxctl.CliError):
                self.run_action({"action": "set_light", "targets": {"refs": ["desk-lamp"]}, "payload": payload})

    def test_dry_run_conflict_rejected(self):
        with self.assertRaises(luxctl.CliError) as ctx:
            self.run_action(
                {"action": "set_light", "targets": {"refs": ["desk-lamp"]}, "payload": {"on": True}, "dry_run": True},
                dry_run=True,
            )
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")

    def test_unknown_action_and_keys(self):
        with self.assertRaises(luxctl.CliError):
            self.run_action({"action": "explode"})
        with self.assertRaises(luxctl.CliError):
            self.run_action({"action": "set_light", "targets": {"bogus": []}, "payload": {"on": True}})

    def test_all_conflicts_with_ref(self):
        with self.assertRaises(luxctl.CliError) as ctx:
            self.run_action(
                {"action": "set_room", "target": {"all": True, "ref": "bedroom"}, "payload": {"on": False}}
            )
        self.assertEqual(ctx.exception.error_type, "invalid_arguments")


class TestArgvHandling(unittest.TestCase):
    def test_normalize_moves_global_flags_forward(self):
        argv = ["set", "light", "--name", "Desk", "--bridge", "1.2.3.4", "--dry-run"]
        self.assertEqual(
            luxctl.normalize_global_flags(argv),
            ["--bridge", "1.2.3.4", "--dry-run", "set", "light", "--name", "Desk"],
        )

    def test_new_global_flags_are_hoisted(self):
        argv = ["get", "light", "--all", "--detail", "--max-bytes", "500"]
        self.assertEqual(
            luxctl.normalize_global_flags(argv),
            ["--detail", "--max-bytes", "500", "get", "light", "--all"],
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
        schema = doc["result"]["schema"]
        self.assertEqual(schema["version"], 2)
        self.assertIn("internal_error", schema["error_types"])
        self.assertIn("selector_required", schema["error_types"])
        self.assertEqual(schema["identifiers"]["primary"], "ref")
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

    def test_list_gating_fires_before_connecting(self):
        # No config and no bridge here: reaching selector_required rather than
        # config_not_found proves the check runs before any network work.
        for argv, expected in (
            (["get", "light"], "selector_required"),
            (["get", "room"], "selector_required"),
            (["get", "scene"], "selector_required"),
            (["--raw", "get", "light", "--all"], "invalid_arguments"),
        ):
            code, doc = run_main(["--config", "/nonexistent/config.yaml", *argv])
            self.assertEqual(code, 1, argv)
            self.assertEqual(doc["error"]["type"], expected, argv)
            self.assertIn("hint", doc["error"]["details"], argv)

    def test_bad_max_bytes_is_json_error(self):
        code, doc = run_main(["--max-bytes", "-5", "get", "light", "--all"])
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
        with self.assertRaises(luxctl.CliError) as ctx:
            luxctl.build_ssl_context("/nonexistent/ca.pem")
        self.assertEqual(ctx.exception.error_type, "config_invalid")


if __name__ == "__main__":
    unittest.main()
