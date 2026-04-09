# luxctl

A JSON-only Philips Hue CLI designed for LLM callers. Stdlib-only Python 3, no dependencies.

## Features

- **JSON-only output** with stable `ok` / `action` / `result|error` / `meta` envelopes
- **Read**: get lights, rooms, and scenes
- **Write**: set light state, set room state (fan-out to member lights), recall scenes
- **Batch support**: `set light` applies one payload to multiple lights in a single call
- **Room writes**: `set room` fans out to member lights with `--exclude` support
- **Structured input**: JSON mutation payloads via `--input-json` or `--input -` (stdin)
- **Dry-run**: validate mutations without sending them to the bridge
- **Color support**: hex colors, CSS color names, CIE xy coordinates, mirek color temperature

## Quick Start

```bash
# Copy and edit config (or use --bridge and --key flags)
cp config.yaml.example ~/.openhue/config.yaml
# Edit ~/.openhue/config.yaml with your bridge IP and application key

# Make executable
chmod +x luxctl

# List all lights
./luxctl get light

# Turn on a light
./luxctl set light --name "Desk lamp" --on true

# Set brightness and color
./luxctl set light --name "Desk lamp" --brightness 50 --hex warmwhite
```

## Configuration

luxctl reads bridge credentials from `~/.openhue/config.yaml` by default:

```yaml
bridge: 192.168.1.100
key: your-hue-application-key
```

You can override the config path with `--config`, or pass credentials directly with `--bridge` and `--key`.

| Flag | Default | Purpose |
|---|---|---|
| `--config` | `~/.openhue/config.yaml` | Path to config file |
| `--bridge` | from config | Bridge IP or hostname |
| `--key` | from config | Hue application key |
| `--connect-timeout` | `5.0` | HTTP connect timeout (seconds) |
| `--read-timeout` | `10.0` | HTTP read timeout (seconds) |

## Usage

### Read commands

```bash
# List all lights
./luxctl get light

# Get a specific light by name
./luxctl get light --name "Desk lamp"

# Get a specific light by ID
./luxctl get light --id <uuid>

# List all rooms
./luxctl get room

# List all scenes (optionally filtered by room)
./luxctl get scene --room-name "Bedroom"
```

### Write commands (flag mode)

```bash
# Turn a light on
./luxctl set light --name "Desk lamp" --on true

# Set brightness (0-100)
./luxctl set light --name "Desk lamp" --brightness 50

# Set color temperature (153-500 mirek)
./luxctl set light --name "Desk lamp" --mirek 300

# Set color by hex
./luxctl set light --name "Desk lamp" --hex "#ff6600"

# Set color by CSS name
./luxctl set light --name "Desk lamp" --hex purple

# Set color by CIE xy
./luxctl set light --name "Desk lamp" --xy 0.3 0.15

# Set multiple lights at once
./luxctl set light --name "Left sconce" --name "Right sconce" --on true --brightness 35

# Set all lights in a room
./luxctl set room --name "Bedroom" --on true --brightness 50

# Set room lights, excluding some
./luxctl set room --name "Bedroom" --on true --exclude "Bedroom Boy"

# Recall a scene
./luxctl set scene --name "Relax" --room-name "Bedroom"

# Dry run (validate without sending)
./luxctl set light --name "Desk lamp" --on true --dry-run
```

### Write commands (JSON input mode)

JSON input is preferred for programmatic and LLM-driven mutations:

```bash
# Inline JSON
./luxctl --input-json '{"action":"set_light","targets":{"names":["Left sconce","Right sconce"]},"payload":{"on":true,"brightness":35}}'

# JSON from stdin
echo '{"action":"set_room","target":{"name":"Bedroom"},"payload":{"on":true},"exclude":["Bedroom Boy"],"dry_run":true}' | ./luxctl --input -

# JSON from file
./luxctl --input request.json
```

#### JSON input schema

**`set_light`:**

```json
{
  "action": "set_light",
  "targets": {"ids": ["..."], "names": ["..."]},
  "payload": {"on": true, "brightness": 40, "mirek": 300, "hex": "#800080", "xy": [0.27, 0.11]},
  "dry_run": false
}
```

**`set_room`:**

```json
{
  "action": "set_room",
  "target": {"id": "...", "name": "Bedroom"},
  "payload": {"on": true},
  "exclude": ["Light name"],
  "dry_run": false
}
```

**`set_scene`:**

```json
{
  "action": "set_scene",
  "target": {"id": "...", "name": "Relax", "room_id": "...", "room_name": "Bedroom"},
  "payload": {"action": "active", "duration_ms": 1000},
  "dry_run": false
}
```

### Introspection

```bash
# List supported actions
./luxctl list-actions

# Get machine-readable schema
./luxctl schema

# Get help as JSON
./luxctl --help
```

## Output Format

All output is JSON. Successful responses:

```json
{
  "ok": true,
  "action": "get_light",
  "result": { ... },
  "meta": {"bridge_ip": "192.168.1.100", "api_version": "v2", "dry_run": false}
}
```

Error responses:

```json
{
  "ok": false,
  "action": "set_light",
  "error": {
    "type": "resource_not_found",
    "message": "No light matched the selector",
    "details": {"name": "Nonexistent"}
  }
}
```

### Error types

| Type | Meaning |
|---|---|
| `invalid_arguments` | Bad CLI arguments or JSON input |
| `config_not_found` | Config file missing |
| `config_invalid` | Config file malformed or missing required fields |
| `bridge_unreachable` | Cannot connect to the Hue bridge |
| `authentication_failed` | Bridge rejected the application key |
| `api_rejected_request` | Bridge returned an HTTP error |
| `resource_not_found` | No light/room/scene matched the selector |
| `ambiguous_match` | Multiple resources matched a name selector |
| `timeout` | Bridge request timed out |
| `unexpected_response` | Bridge returned invalid JSON |

## Supported CSS Color Names

`black`, `white`, `red`, `green`, `blue`, `yellow`, `orange`, `purple`, `pink`, `cyan`, `magenta`, `warmwhite`, `coolwhite`
