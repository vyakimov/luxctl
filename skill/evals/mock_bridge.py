#!/usr/bin/env python3
"""A fake Hue V2 bridge for evaluating luxctl callers.

Serves a realistic 40-light / 6-room / 24-scene inventory over HTTPS with a
self-signed certificate (which is what a real bridge does, and what luxctl
tolerates by default). Every request is appended to a JSONL log, so a run can
be graded on what the caller actually did rather than on what it says it did.

Usage:
    python3 mock_bridge.py --port 9001 --log /path/to/requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import ssl
import subprocess
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

EFFECTS = ["no_effect", "candle", "fire", "prism", "sparkle", "opal",
           "glisten", "underwater", "cosmos", "sunbeam", "enchant"]

ROOM_NAMES = ["Living room", "Kitchen", "Bedroom", "Office", "Hallway", "Bathroom"]
LIGHT_NAMES = [
    ["Ceiling", "Floor lamp", "TV backlight", "Reading lamp", "Corner lamp", "Shelf strip", "Alcove"],
    ["Ceiling", "Counter strip", "Island pendant", "Under cabinet", "Pantry"],
    ["Ceiling", "Left sconce", "Right sconce", "Night light", "Wardrobe", "Reading lamp"],
    ["Desk lamp", "Ceiling", "Monitor backlight", "Bookshelf"],
    ["Ceiling", "Entry spot", "Stair strip", "Coat nook"],
    ["Ceiling", "Mirror strip", "Shower spot", "Vanity"],
]


def build_inventory(seed: int = 7) -> dict[str, list[dict]]:
    """Deterministic inventory so every run grades against identical state."""
    rng = seed
    def next_bool() -> bool:
        nonlocal rng
        rng = (rng * 1103515245 + 12345) % (2 ** 31)
        return (rng >> 16) % 3 != 0  # roughly two thirds on

    devices, lights, rooms, scenes, grouped = [], [], [], [], []
    for room_index, room_name in enumerate(ROOM_NAMES):
        room_id = str(uuid.UUID(int=room_index + 1000))
        grouped_id = str(uuid.UUID(int=room_index + 2000))
        children = []
        for light_index, light_name in enumerate(LIGHT_NAMES[room_index]):
            n = room_index * 10 + light_index
            light_id = str(uuid.UUID(int=n + 3000))
            device_id = str(uuid.UUID(int=n + 4000))
            on = next_bool()
            lights.append({
                "id": light_id, "id_v1": f"/lights/{n}",
                "owner": {"rid": device_id, "rtype": "device"},
                "metadata": {"name": light_name, "archetype": "table_shade", "function": "mixed"},
                "product_data": {"function": "mixed"}, "identify": {}, "service_id": 0,
                "on": {"on": on},
                "dimming": {"brightness": 40.0 + (n % 5) * 12.5, "min_dim_level": 0.2},
                "dimming_delta": {},
                "color_temperature": {"mirek": 300 + (n % 7) * 20, "mirek_valid": True,
                                      "mirek_schema": {"mirek_minimum": 153, "mirek_maximum": 500}},
                "color_temperature_delta": {},
                "color": {"xy": {"x": 0.4573, "y": 0.4099},
                          "gamut": {"red": {"x": 0.6915, "y": 0.3083},
                                    "green": {"x": 0.17, "y": 0.7},
                                    "blue": {"x": 0.1532, "y": 0.0475}},
                          "gamut_type": "C"},
                "dynamics": {"status": "none", "status_values": ["none", "dynamic_palette"],
                             "speed": 0.0, "speed_valid": False},
                "alert": {"action_values": ["breathe"]},
                "signaling": {"signal_values": ["no_signal", "on_off", "on_off_color", "alternating"]},
                "mode": "normal",
                "effects": {"status_values": EFFECTS, "status": "no_effect", "effect_values": EFFECTS},
                "timed_effects": {"status_values": ["no_effect", "sunrise", "sunset"],
                                  "status": "no_effect",
                                  "effect_values": ["no_effect", "sunrise", "sunset"]},
                "powerup": {"preset": "last_on_state", "configured": True,
                            "on": {"mode": "on", "on": {"on": True}},
                            "dimming": {"mode": "previous"}, "color": {"mode": "previous"}},
                "type": "light",
            })
            devices.append({
                "id": device_id, "id_v1": f"/lights/{n}", "type": "device",
                "product_data": {"model_id": "LCA001", "manufacturer_name": "Signify Netherlands B.V.",
                                 "product_name": "Hue color lamp", "product_archetype": "sultan_bulb",
                                 "certified": True, "software_version": "1.122.2",
                                 "hardware_platform_type": "100b-125"},
                "metadata": {"name": light_name, "archetype": "sultan_bulb"}, "identify": {},
                "services": [{"rid": light_id, "rtype": "light"},
                             {"rid": str(uuid.UUID(int=n + 5000)), "rtype": "zigbee_connectivity"},
                             {"rid": str(uuid.UUID(int=n + 6000)), "rtype": "entertainment"}],
            })
            children.append({"rid": device_id, "rtype": "device"})

        grouped.append({"id": grouped_id, "type": "grouped_light", "on": {"on": True},
                        "dimming": {"brightness": 55.0}, "alert": {"action_values": ["breathe"]},
                        "signaling": {"signal_values": ["no_signal", "on_off"]}})
        rooms.append({"id": room_id, "id_v1": f"/groups/{room_index}", "type": "room",
                      "children": children,
                      "services": [{"rid": grouped_id, "rtype": "grouped_light"}],
                      "metadata": {"name": room_name, "archetype": "living_room"}})

        member_ids = [child["rid"] for child in children]
        for scene_index, scene_name in enumerate(["Relax", "Bright", "Nightlight", "Concentrate"]):
            scenes.append({
                "id": str(uuid.UUID(int=room_index * 10 + scene_index + 7000)), "type": "scene",
                "metadata": {"name": scene_name},
                "group": {"rid": room_id, "rtype": "room"},
                "actions": [{"target": {"rid": mid, "rtype": "light"},
                             "action": {"on": {"on": True}, "dimming": {"brightness": 80.0},
                                        "color": {"xy": {"x": 0.4, "y": 0.4}},
                                        "color_temperature": {"mirek": 300}}}
                            for mid in member_ids],
                "palette": {"color": [], "dimming": [{"brightness": 60.0}],
                            "color_temperature": [], "effects": []},
                "speed": 0.5, "auto_dynamic": False, "status": {"active": "inactive"},
            })

    return {"device": devices, "light": lights, "room": rooms,
            "scene": scenes, "grouped_light": grouped}


def make_cert() -> tuple[str, str]:
    tmp = Path(tempfile.mkdtemp(prefix="mockbridge-"))
    cert, keyfile = tmp / "cert.pem", tmp / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(keyfile), "-out", str(cert), "-days", "1",
         "-subj", "/CN=mockbridge"],
        check=True, capture_output=True,
    )
    return str(cert), str(keyfile)


def make_handler(inventory: dict, log_path: Path):
    state = {resource: {item["id"]: item for item in items} for resource, items in inventory.items()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # silence stderr chatter
            pass

        def record(self, method: str, body):
            with log_path.open("a") as handle:
                handle.write(json.dumps({"method": method, "path": self.path, "body": body}) + "\n")

        def respond(self, payload: dict, status: int = 200):
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            self.record("GET", None)
            parts = self.path.strip("/").split("/")
            # /clip/v2/resource/<type>
            if len(parts) == 4 and parts[2] == "resource" and parts[3] in inventory:
                return self.respond({"errors": [], "data": inventory[parts[3]]})
            return self.respond({"errors": [{"description": "not found"}], "data": []}, 404)

        def do_PUT(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or "{}")
            except json.JSONDecodeError:
                body = {"_unparsed": raw.decode("utf-8", "replace")}
            self.record("PUT", body)

            parts = self.path.strip("/").split("/")
            # /clip/v2/resource/<type>/<id>
            if len(parts) == 5 and parts[2] == "resource":
                rtype, rid = parts[3], parts[4]
                target = state.get(rtype, {}).get(rid)
                if target is None:
                    return self.respond({"errors": [{"description": "resource not found"}], "data": []}, 404)
                if rtype == "light":
                    for key, value in body.items():
                        target[key] = {**target.get(key, {}), **value} if isinstance(value, dict) else value
                return self.respond({"errors": [], "data": [{"rid": rid, "rtype": rtype}]})
            return self.respond({"errors": [{"description": "not found"}], "data": []}, 404)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")

    inventory = build_inventory()
    cert, keyfile = make_cert()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, keyfile)

    server = HTTPServer(("127.0.0.1", args.port), make_handler(inventory, log_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"mock bridge on 127.0.0.1:{args.port}, logging to {log_path}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
