# luxctl

A JSON-only Philips Hue CLI built for machines. Single-file Python 3, zero dependencies.

## Why luxctl?

Traditional Hue CLIs like [openhue](https://github.com/openhue/openhue-cli) are built for humans: formatted tables, colored terminal output, interactive prompts. That's great at a keyboard, but it falls apart the moment an LLM or script tries to use one. The model has to parse free-form text, guess at column boundaries, and hope the output format doesn't change between versions.

luxctl takes the opposite approach. Every response — success, error, help, even `--help` — is a JSON object with a stable envelope:

```json
{"ok": true, "action": "get_light", "result": { ... }, "meta": { ... }}
```

This means:

- **No parsing** — the caller reads structured fields, not screen-scraped text.
- **Typed errors** — every failure carries an `error.type` string (`bridge_unreachable`, `resource_not_found`, `ambiguous_match`, ...) so the caller can branch on failure mode without string-matching error messages.
- **Structured input** — mutations accept a JSON payload via `--input-json` or stdin, so the caller never has to shell-escape a complex command.
- **Self-describing** — `luxctl schema` returns the full action schema as JSON. An LLM can read it once and know exactly what it can call and with what parameters.
- **Dry-run** — every mutation supports `--dry-run`, returning exactly what would be sent to the bridge. An LLM can preview its own intent before committing.
- **Deterministic** — no prompts, no pagination, no color codes, no interactive mode. Same input, same structure out.

If you're wiring Hue control into an agent, a home automation pipeline, or anything that consumes stdout programmatically, luxctl is the tool that doesn't make you write a wrapper around a wrapper.

## Quick Start

```bash
# Make executable
chmod +x luxctl

# Pair with your bridge (see "Bridge Pairing" below), then:
./luxctl get light
./luxctl set light --name "Desk lamp" --on true --brightness 50 --hex warmwhite
```

## Bridge Pairing

luxctl talks directly to the Hue Bridge V2 API over HTTPS on your local network. Before you can use it, you need a bridge IP and an application key.

### 1. Find your bridge

Check your router's DHCP leases, or use Hue's discovery endpoint:

```bash
curl -s https://discovery.meethue.com | python3 -m json.tool
```

### 2. Create an application key

Press the physical link button on your Hue bridge, then within 30 seconds run:

```bash
curl -sk -X POST "https://<BRIDGE_IP>/api" \
  -H "Content-Type: application/json" \
  -d '{"devicetype": "luxctl#myhost", "generateclientkey": true}'
```

The response contains your `username` — that's the application key.

### 3. Save credentials

```bash
mkdir -p ~/.openhue
cat > ~/.openhue/config.yaml <<EOF
bridge: <BRIDGE_IP>
key: <APPLICATION_KEY>
EOF
```

Or pass them directly: `./luxctl --bridge <IP> --key <KEY> get light`

## Configuration

luxctl reads bridge credentials from `~/.openhue/config.yaml` by default:

```yaml
bridge: 192.168.1.100
key: your-hue-application-key
```

Override with `--config`, `--bridge`, or `--key`.

| Flag | Default | Purpose |
|---|---|---|
| `--config` | `~/.openhue/config.yaml` | Path to config file |
| `--bridge` | from config | Bridge IP or hostname |
| `--key` | from config | Hue application key |
| `--connect-timeout` | `5.0` | HTTP connect timeout (seconds) |
| `--read-timeout` | `10.0` | HTTP read timeout (seconds) |

## Usage

### Read

```bash
./luxctl get light                              # all lights
./luxctl get light --name "Desk lamp"           # by name
./luxctl get light --id <uuid>                  # by ID
./luxctl get room                               # all rooms
./luxctl get scene --room-name "Bedroom"        # scenes in a room
```

### Write (flag mode)

```bash
./luxctl set light --name "Desk lamp" --on true
./luxctl set light --name "Desk lamp" --brightness 50 --mirek 300
./luxctl set light --name "Desk lamp" --hex "#ff6600"
./luxctl set light --name "Desk lamp" --hex purple
./luxctl set light --name "Desk lamp" --xy 0.3 0.15

# Multiple lights in one call
./luxctl set light --name "Left sconce" --name "Right sconce" --on true --brightness 35

# All lights in a room
./luxctl set room --name "Bedroom" --on true --brightness 50

# Exclude specific lights
./luxctl set room --name "Bedroom" --on true --exclude "Night light"

# Recall a scene
./luxctl set scene --name "Relax" --room-name "Bedroom"

# Preview without sending
./luxctl set light --name "Desk lamp" --on true --dry-run
```

### Write (JSON input mode)

Preferred for programmatic callers — no shell escaping needed:

```bash
# Inline
./luxctl --input-json '{"action":"set_light","targets":{"names":["Left sconce","Right sconce"]},"payload":{"on":true,"brightness":35}}'

# From stdin
echo '{"action":"set_room","target":{"name":"Bedroom"},"payload":{"on":true},"exclude":["Night light"],"dry_run":true}' | ./luxctl --input -

# From file
./luxctl --input request.json
```

#### JSON actions

**`set_light`** — one payload, one or more targets:

```json
{
  "action": "set_light",
  "targets": {"ids": ["..."], "names": ["..."]},
  "payload": {"on": true, "brightness": 40, "mirek": 300, "hex": "#800080"},
  "dry_run": false
}
```

**`set_room`** — fan-out to all lights in a room:

```json
{
  "action": "set_room",
  "target": {"name": "Bedroom"},
  "payload": {"on": true},
  "exclude": ["Night light"],
  "dry_run": false
}
```

**`set_scene`** — recall a scene:

```json
{
  "action": "set_scene",
  "target": {"name": "Relax", "room_name": "Bedroom"},
  "payload": {"action": "active", "duration_ms": 1000},
  "dry_run": false
}
```

### Introspection

```bash
./luxctl list-actions    # supported actions and parameters
./luxctl schema          # full machine-readable schema
./luxctl --help          # help tree (also JSON)
```

## Output Format

Every response is a JSON object. No exceptions.

**Success:**

```json
{
  "ok": true,
  "action": "get_light",
  "result": { "lights": [ ... ] },
  "meta": {"bridge_ip": "192.168.1.100", "api_version": "v2", "dry_run": false}
}
```

**Error:**

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

## Color Support

Set colors with `--hex` (hex codes or CSS names), `--xy` (CIE coordinates), or `--mirek` (color temperature).

Supported CSS names: `black`, `white`, `red`, `green`, `blue`, `yellow`, `orange`, `purple`, `pink`, `cyan`, `magenta`, `warmwhite`, `coolwhite`
