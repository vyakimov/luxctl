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

But parseability is only half the problem. A response that parses perfectly and doesn't fit in the caller's context window is still a failed call. So luxctl is also built to be *small*:

- **Compact by default** — responses carry the fields needed to decide the next action. Detail and raw bridge data are opt-in.
- **Budgeted** — every list response is capped and says so in-band when it was cut, so a caller can never mistake a truncated list for a complete one.
- **Semantic identifiers** — resources are addressed by readable refs like `kitchen-ceiling`, not UUIDs.
- **Filtered at the source** — ask for the lights that are on, not for every light.

On a 40-light bridge, `get light --all` returns **3.3 KB**. The same call in 0.2.x returned **70 KB**.

If you're wiring Hue control into an agent, a home automation pipeline, or anything that consumes stdout programmatically, luxctl is the tool that doesn't make you write a wrapper around a wrapper.

## Quick Start

```bash
# Make executable
chmod +x luxctl

# Pair with your bridge (see "Bridge Pairing" below), then:
./luxctl get light --all
./luxctl set light --ref desk-lamp --on true --brightness 50 --hex warmwhite

# Turn everything off, in one call, with no read at all
./luxctl set light --all --on false
```

## Identifiers: refs

Every resource has a **ref** — a slug derived from its name, and the identifier you should normally use:

```json
{"ref": "desk-lamp", "name": "Desk lamp", "on": true, "brightness": 62, "room": "office"}
```

- Unique names get a bare ref: `Desk lamp` → `desk-lamp`.
- Colliding names are qualified by room: two lights called `Lamp` become `office/lamp` and `bedroom/lamp`.
- A duplicate name inside one room falls back to a numeric suffix (`kitchen/lamp`, `kitchen/lamp-2`), assigned in UUID order so it is stable for a given bridge state.

Refs are derived from names and resolved fresh on every call, so **renaming a light changes its ref**. They are identifiers for a conversation, not durable keys. If you need a stable handle, use `--detail` to get the bridge `uuid` and select with `--id`.

All three selectors work everywhere: `--ref` (preferred), `--name`, `--id`.

## Response size

Responses are capped by `--max-bytes` (default `8192`; `0` disables). Every list result carries three fields, always:

```json
{"lights": [...], "count": 25, "total": 40, "truncated": true,
 "hint": "Truncated to 25 of 40 lights by the 8192B budget. Narrow with a selector, --room, --on, or --limit; or raise --max-bytes."}
```

`truncated` is present whether or not it fired. If it is `true`, the list is incomplete — narrow the query rather than acting on what you got. The truncation notice is never dropped to hit a budget, so the envelope plus that notice is a floor of roughly 400 bytes.

### Verbosity

| Level | Flag | Contents |
|---|---|---|
| compact | *(default)* | `ref`, `name`, `on`, `brightness`, `room` |
| detail | `--detail` | + `uuid`, exact brightness, `mirek`, `xy`, room linkage |
| raw | `--raw` | + the untouched bridge resource |

`--raw` requires a selector. Raw output is only available for a single resource, so an unbounded raw list cannot be requested.

`--verbose` is separate: it adds per-target write echoes to mutations. Write *failures* are always reported regardless, since they are the one case the caller has to act on.

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

Or pass them directly: `./luxctl --bridge <IP> --key <KEY> get light --all`

## Configuration

luxctl reads bridge credentials from `~/.openhue/config.yaml` by default:

```yaml
bridge: 192.168.1.100
key: your-hue-application-key
# ca_cert: ~/.openhue/bridge-cert.pem   # optional, enables TLS verification
```

Credentials resolve in priority order: command-line flags, then environment variables (`LUXCTL_BRIDGE`, `LUXCTL_KEY`, `LUXCTL_CA_CERT`), then the config file. If both bridge and key are supplied via flags or environment, no config file is needed.

| Flag | Default | Purpose |
|---|---|---|
| `--config` | `~/.openhue/config.yaml` | Path to config file |
| `--bridge` | `LUXCTL_BRIDGE` env, then config | Bridge IP or hostname |
| `--key` | `LUXCTL_KEY` env, then config | Hue application key |
| `--ca-cert` | `LUXCTL_CA_CERT` env, then config | CA/bridge certificate for TLS verification |
| `--connect-timeout` | `5.0` | HTTP connect timeout (seconds) |
| `--read-timeout` | `10.0` | HTTP read timeout (seconds) |
| `--max-bytes` | `8192` | Response size budget (0 disables) |
| `--detail` | off | Return the detailed view |
| `--raw` | off | Return the raw bridge resource (requires a selector) |
| `--verbose` | off | Include per-target write echoes |
| `--version` | | Print the luxctl version as JSON |

### TLS verification

Hue bridges serve a self-signed certificate by default, so luxctl skips certificate verification unless you opt in. To pin your bridge's certificate:

```bash
# Capture the bridge certificate once
openssl s_client -connect <BRIDGE_IP>:443 -showcerts </dev/null 2>/dev/null \
  | openssl x509 > ~/.openhue/bridge-cert.pem

# Verify against it on every call
./luxctl --ca-cert ~/.openhue/bridge-cert.pem get light --all
```

With a pinned certificate the chain is verified (a mismatch fails with error type `tls_verification_failed`); hostname verification stays off because the bridge certificate's common name is the bridge ID, not its IP.

## Usage

### Read

Listing a whole collection is deliberate: it needs `--all` or a filter. Without one you get a `selector_required` error rather than a large response you didn't intend to ask for.

```bash
./luxctl get light --all                        # every light, compact
./luxctl get light --on true                     # only the lights that are on
./luxctl get light --on false                    # only the lights that are off
./luxctl get light --room kitchen                # lights in one room
./luxctl get light --all --count                 # just the number
./luxctl get light --all --limit 10              # first 10, total still reported
./luxctl get light --ref desk-lamp               # one light
./luxctl --detail get light --ref desk-lamp      # ...with colour state and uuid
./luxctl --raw get light --ref desk-lamp         # ...with the bridge resource

./luxctl get room --all                          # rooms, compact
./luxctl get room --ref bedroom --with-lights    # one room and its members
./luxctl get room --ref bedroom --with-scenes    # one room and its scenes

./luxctl get scene --room bedroom                # scenes in a room
./luxctl get scene --all                         # every scene
```

Scenes are not embedded in room responses unless you ask with `--with-scenes`, and the scene collection is only fetched from the bridge when a command actually needs it.

### Write (flag mode)

```bash
./luxctl set light --ref desk-lamp --on true
./luxctl set light --ref desk-lamp --brightness 50 --mirek 300
./luxctl set light --ref desk-lamp --hex "#ff6600"
./luxctl set light --ref desk-lamp --hex purple
./luxctl set light --ref desk-lamp --xy 0.3 0.15

# Multiple lights in one call
./luxctl set light --ref left-sconce --ref right-sconce --on true --brightness 35

# Every light on the bridge
./luxctl set light --all --on false

# All lights in a room, or in every room
./luxctl set room --ref bedroom --on true --brightness 50
./luxctl set room --all --on false

# Exclude specific lights (resolved across whatever the scope is)
./luxctl set room --ref bedroom --on true --exclude night-light
./luxctl set room --all --on false --exclude night-light

# Recall a scene
./luxctl set scene --ref bedroom/relax
./luxctl set scene --name Relax --room bedroom

# Preview without sending
./luxctl set light --ref desk-lamp --on true --dry-run
```

A successful write reports the outcome, not the resulting state:

```json
{"ok": true, "action": "set_room", "result": {
  "room": {"ref": "bedroom", "name": "Bedroom"},
  "applied": {"on": {"on": false}},
  "summary": {"target_count": 4, "success_count": 4, "failure_count": 0, "dry_run": false}
}, "meta": {...}}
```

If any target fails, a `failures` array is added listing each failed target and its typed error. Pass `--verbose` for the full per-target `writes` array.

### Write (JSON input mode)

Preferred for programmatic callers — no shell escaping needed:

```bash
# Inline
./luxctl --input-json '{"action":"set_light","targets":{"refs":["left-sconce","right-sconce"]},"payload":{"on":true,"brightness":35}}'

# From stdin
echo '{"action":"set_room","target":{"ref":"bedroom"},"payload":{"on":true},"exclude":["night-light"],"dry_run":true}' | ./luxctl --input -

# From file
./luxctl --input request.json
```

#### JSON actions

**`set_light`** — one payload, one or more targets:

```json
{
  "action": "set_light",
  "targets": {"refs": ["desk-lamp"], "names": ["..."], "ids": ["..."], "all": false},
  "payload": {"on": true, "brightness": 40, "mirek": 300, "hex": "#800080"},
  "dry_run": false
}
```

**`set_room`** — fan-out to all lights in a room, or every room with `"all": true`:

```json
{
  "action": "set_room",
  "target": {"ref": "bedroom"},
  "payload": {"on": true},
  "exclude": ["night-light"],
  "dry_run": false
}
```

**`set_scene`** — recall a scene:

```json
{
  "action": "set_scene",
  "target": {"ref": "bedroom/relax", "room": "bedroom"},
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
  "result": {"lights": [ ... ], "count": 12, "total": 12, "truncated": false},
  "meta": {"bridge_ip": "192.168.1.100", "api_version": "v2", "dry_run": false, "view": "compact"}
}
```

**Error:**

```json
{
  "ok": false,
  "action": "set_light",
  "error": {
    "type": "resource_not_found",
    "message": "No light matched the ref",
    "details": {"ref": "nonexistent", "hint": "Refs are derived from names; list them with `get light --all`."}
  }
}
```

Bridge payloads echoed into `error.details` are capped, so a misbehaving bridge cannot blow the caller's context.

### Error types

| Type | Meaning |
|---|---|
| `invalid_arguments` | Bad CLI arguments or JSON input |
| `selector_required` | A selector, filter, or `--all` is required for this call |
| `config_not_found` | Config file missing |
| `config_invalid` | Config file malformed or missing required fields |
| `bridge_unreachable` | Cannot connect to the Hue bridge |
| `tls_verification_failed` | Bridge certificate failed verification against `--ca-cert` |
| `authentication_failed` | Bridge rejected the application key |
| `api_rejected_request` | Bridge returned an HTTP error |
| `resource_not_found` | No light/room/scene matched the selector |
| `ambiguous_match` | Multiple resources matched a name selector |
| `timeout` | Bridge request timed out |
| `unexpected_response` | Bridge returned invalid JSON |
| `internal_error` | Unexpected failure inside luxctl (still reported as JSON) |

## Color Support

Set colors with `--hex` (hex codes or CSS names), `--xy` (CIE coordinates), or `--mirek` (color temperature).

Supported CSS names: `black`, `white`, `red`, `green`, `blue`, `yellow`, `orange`, `purple`, `pink`, `cyan`, `magenta`, `warmwhite`, `coolwhite`

## Upgrading from 0.2.x

0.3.0 changes the response contract (`schema.version` 1 → 2). If you have a caller pinned to the old shape:

- **`raw` is gone from list responses.** Use `--raw` with a selector for a single resource.
- **`id` is now `ref`** in responses. The bridge UUID is available as `uuid` under `--detail`. `--id` still accepts UUIDs as a selector.
- **Listing requires `--all`** or a filter. `get light` alone now returns `selector_required`.
- **Write responses report outcomes**, not resource state. `bridge_response` and per-target `writes` moved behind `--verbose`.
- **`set_room` no longer emits `target_lights` and `targets`** (they were duplicates). Excluded lights are listed under `excluded` when non-empty.
- **`--room-id` / `--room-name` collapsed into `--room`**, which accepts a ref, a name, or a UUID.
- **List results always carry `count`, `total`, and `truncated`.**

## Development

luxctl is a single file with no dependencies beyond the Python 3.10+ standard library. Tests use `unittest` and run without a bridge:

```bash
python3 -m unittest discover -s tests -v
```
