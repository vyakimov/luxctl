# luxctl reference

The long tail. `SKILL.md` covers the common surface; this file covers everything else.

## Contents

- [Global flags](#global-flags)
- [Command reference](#command-reference)
- [Identifiers and refs](#identifiers-and-refs)
- [Views: compact, detail, raw](#views-compact-detail-raw)
- [Response budget and truncation](#response-budget-and-truncation)
- [Response shapes](#response-shapes)
- [JSON input mode](#json-input-mode)
- [Complete error table](#complete-error-table)
- [Colour handling](#colour-handling)
- [Configuration and TLS](#configuration-and-tls)
- [Performance notes](#performance-notes)
- [Introspection](#introspection)
- [Migrating from 0.2.x](#migrating-from-02x)

## Global flags

Global flags may appear anywhere on the command line; they are hoisted before parsing.

| Flag | Default | Purpose |
|---|---|---|
| `--config` | `~/.openhue/config.yaml` | Path to config file |
| `--bridge` | `LUXCTL_BRIDGE` env, then config | Bridge IP or hostname |
| `--key` | `LUXCTL_KEY` env, then config | Hue application key |
| `--ca-cert` | `LUXCTL_CA_CERT` env, then config | CA/bridge certificate for TLS verification |
| `--connect-timeout` | `5.0` | HTTP connect timeout (seconds) |
| `--read-timeout` | `10.0` | HTTP read timeout (seconds) |
| `--max-bytes` | `8192` | Response size budget; `0` disables |
| `--detail` | off | Detailed view |
| `--raw` | off | Raw bridge resource; requires a selector |
| `--verbose` | off | Per-target write echoes on mutations |
| `--dry-run` | off | Validate and return the planned payload without sending |
| `--input-json` | — | Inline JSON mutation payload |
| `--input` | — | JSON payload from a file path, or `-` for stdin |
| `--version` | — | Version as JSON |
| `--help` | — | Help tree as JSON |

## Command reference

Selectors are `--ref` (preferred), `--name`, and `--id` (bridge uuid). Supplying more than one is `invalid_arguments`.

### `get light`

| Flag | Notes |
|---|---|
| `--ref` / `--name` / `--id` | Return one light |
| `--all` | List every light |
| `--on true\|false` | Filter by power state; satisfies the listing rule on its own |
| `--room <ref\|name\|uuid>` | Filter by room; satisfies the listing rule on its own |
| `--limit N` | Return at most N; `total` still reports the full match count |
| `--count` | Return `{"count": N, "total": N}` only |

### `get room`

| Flag | Notes |
|---|---|
| `--ref` / `--name` / `--id` | Return one room |
| `--all` | List every room |
| `--with-lights` | Include compact member lights |
| `--with-scenes` | Include compact scenes |
| `--limit N`, `--count` | As above |

### `get scene`

| Flag | Notes |
|---|---|
| `--ref` / `--name` / `--id` | Return one scene |
| `--all` | List every scene |
| `--room <ref\|name\|uuid>` | Filter by room; satisfies the listing rule on its own |
| `--limit N`, `--count` | As above |

### `set light`

| Flag | Notes |
|---|---|
| `--ref` / `--name` / `--id` | Repeatable; targets accumulate and de-duplicate |
| `--all` | Every light on the bridge, including room-less ones. Cannot combine with a selector |
| `--on true\|false` | |
| `--brightness 0-100` | |
| `--mirek 153-500` | Colour temperature; lower is cooler |
| `--xy X Y` | CIE coordinates, each 0–1 |
| `--hex` | `#RRGGBB` or a colour name. Mutually exclusive with `--xy` |

At least one state field is required, otherwise `invalid_arguments`.

### `set room`

Same state fields as `set light`, plus:

| Flag | Notes |
|---|---|
| `--ref` / `--name` / `--id` | One room (not repeatable) |
| `--all` | Every room's lights. Cannot combine with a selector |
| `--exclude` | Repeatable. Accepts a ref, name, or uuid; resolves against the whole scope |

### `set scene`

| Flag | Notes |
|---|---|
| `--ref` / `--name` / `--id` | The scene |
| `--room <ref\|name\|uuid>` | Disambiguates scenes that share a name across rooms |
| `--action` | `active` (default), `dynamic_palette`, or `static` |
| `--duration-ms N` | Transition time, non-negative integer |

## Identifiers and refs

A ref is `slugify(name)`: lowercased, non-alphanumeric runs collapsed to `-`, trimmed. `Desk lamp` → `desk-lamp`.

Collision handling, in order:

1. **Unique slug** → bare ref (`desk-lamp`).
2. **Slug collides** → qualify by room (`office/lamp`, `bedroom/lamp`).
3. **Still collides** (same name, same room) → numeric suffix assigned in uuid order (`kitchen/lamp`, `kitchen/lamp-2`). Ordering by uuid keeps suffixes stable for a given bridge state.

Rooms resolve first, then lights and scenes qualify against room names.

Refs are recomputed on every invocation from current names. Renaming a resource changes its ref, and a stale ref fails loudly with `resource_not_found` rather than silently hitting the wrong light. If you need a durable handle, take `uuid` from `--detail` and select with `--id`.

`--name` matching is case- and whitespace-insensitive. If a name matches several resources you get `ambiguous_match` with a `matches` array of refs — retry with one of them.

## Views: compact, detail, raw

| View | Flag | Light fields |
|---|---|---|
| compact | *(default)* | `ref`, `name`, `on`, `brightness` (rounded), `room` |
| detail | `--detail` | + `uuid`, `brightness_exact`, `mirek`, `mirek_valid`, `xy`, `rooms`, `device_uuid` |
| raw | `--raw` | + `raw` (the untouched bridge resource) |

Room compact is `ref`, `name`, `on`, `light_count`; `--detail` adds `uuid`, `grouped_light_uuid`, `scene_count`. Scene compact is `ref`, `name`, `room`; `--detail` adds `uuid`, `room_name`, `status`, `speed`, `auto_dynamic`.

`--raw` requires a selector. This is structural: a raw list cannot be requested at all, so the worst case is one resource. `meta.view` echoes which view produced the response.

Nested lights and scenes inside a room follow the same view flag but never include `raw`.

## Response budget and truncation

`--max-bytes` (default 8192) caps list responses. When the encoded response exceeds the budget, luxctl trims items off the end and rewrites the result:

```json
{"lights": [...], "count": 25, "total": 40, "truncated": true,
 "hint": "Truncated to 25 of 40 lights by the 8192B budget. Narrow with a selector, --room, --on, or --limit; or raise --max-bytes."}
```

`count`, `total`, and `truncated` are present on every list response whether or not trimming occurred, so `truncated` is always safe to read.

The truncation notice is never dropped to hit a budget. That means the envelope plus the notice is a floor of roughly 400 bytes: a budget below that is approached as closely as possible but not met. This is deliberate — losing the "incomplete" signal to save bytes would be worse than overshooting.

Single-resource responses are not trimmed. `--max-bytes 0` disables trimming entirely.

## Response shapes

### Write, single target

```json
{"result": {
  "target": {"ref": "desk-lamp", "name": "Desk lamp"},
  "applied": {"on": {"on": false}},
  "summary": {"target_count": 1, "success_count": 1, "failure_count": 0, "dry_run": false}}}
```

### Write, multiple targets

`targets` replaces `target` and holds an array of `{ref, name}`.

### Write, `--all`

`scope` replaces the target list: `"all_lights"` for `set light --all`, `"all_rooms"` (plus `room_count`) for `set room --all`. No per-light list is emitted, which is what keeps a 40-light write at ~260 bytes.

### `set room`

Adds `room: {ref, name}`, and `excluded: [{ref, name}]` when exclusions applied.

### `set scene`

```json
{"result": {
  "scene": {"ref": "bedroom/relax", "name": "Relax", "room": "bedroom"},
  "applied": {"recall": {"action": "active"}},
  "summary": {"ok": true}}}
```

### Failures

Any target that fails adds an entry to `failures`, alongside a non-zero `summary.failure_count`:

```json
{"failures": [{"target": {"ref": "right-sconce", "name": "Right sconce"},
               "error": {"type": "api_rejected_request", "message": "...", "details": {...}}}]}
```

Failures appear regardless of `--verbose`. `--verbose` additionally emits `writes`, the full per-target array including each successful `bridge_response`.

### Dry run

`payload` replaces `applied`, `summary.dry_run` is `true`, `meta.dry_run` is `true`, and no bridge request is made.

## JSON input mode

For programmatic callers — avoids shell escaping entirely. Cannot be combined with subcommand arguments.

```bash
luxctl --input-json '{"action":"set_light","targets":{"refs":["desk-lamp"]},"payload":{"on":true}}'
echo '{...}' | luxctl --input -
luxctl --input request.json
```

Unknown keys are rejected with `invalid_arguments`, so typos fail loudly.

**`set_light`**

```json
{
  "action": "set_light",
  "targets": {"refs": ["..."], "names": ["..."], "ids": ["..."], "all": false},
  "payload": {"on": true, "brightness": 40, "mirek": 300, "hex": "#800080", "xy": [0.27, 0.11]},
  "dry_run": false,
  "verbose": false
}
```

**`set_room`**

```json
{
  "action": "set_room",
  "target": {"ref": "bedroom", "name": "...", "id": "...", "all": false},
  "payload": {"on": true},
  "exclude": ["night-light"],
  "dry_run": false
}
```

**`set_scene`**

```json
{
  "action": "set_scene",
  "target": {"ref": "bedroom/relax", "name": "Relax", "id": "...", "room": "bedroom"},
  "payload": {"action": "active", "duration_ms": 1000},
  "dry_run": false
}
```

`dry_run` may be set in the document or via `--dry-run`, but not both.

## Complete error table

| Type | Meaning |
|---|---|
| `invalid_arguments` | Bad CLI arguments or JSON input |
| `selector_required` | A selector, filter, or `--all` is required for this call |
| `config_not_found` | Config file missing |
| `config_invalid` | Config file malformed, missing required fields, or an unloadable CA cert |
| `bridge_unreachable` | Cannot connect to the bridge |
| `tls_verification_failed` | Certificate failed verification against `--ca-cert` |
| `authentication_failed` | Bridge rejected the application key (HTTP 401/403) |
| `api_rejected_request` | Bridge returned an HTTP error |
| `resource_not_found` | No light/room/scene matched the selector |
| `ambiguous_match` | Multiple resources matched a name selector |
| `timeout` | Bridge request timed out |
| `unexpected_response` | Bridge returned invalid JSON |
| `internal_error` | Unexpected failure inside luxctl, still reported as JSON |

Bridge payloads echoed into `error.details` are capped at 1024 characters; an oversized payload is replaced by `{"truncated": true, "original_chars": N, "preview": "..."}`.

## Colour handling

Three mutually informative ways to set colour:

- `--hex` — `#RRGGBB`, bare `RRGGBB`, or a name from: `black`, `white`, `red`, `green`, `blue`, `yellow`, `orange`, `purple`, `pink`, `cyan`, `magenta`, `warmwhite`, `coolwhite`. Converted to CIE xy via gamma-corrected sRGB.
- `--xy X Y` — CIE 1931 coordinates, each 0–1.
- `--mirek N` — colour temperature, 153–500. Lower is cooler/bluer, higher is warmer/oranger. 153 ≈ 6500K, 500 ≈ 2000K.

`--hex` and `--xy` are mutually exclusive. `--mirek` combines freely with brightness and on-state, but setting both a colour and a temperature in one call lets the bridge decide precedence — prefer one or the other.

## Configuration and TLS

Credentials resolve in order: command-line flags → environment variables (`LUXCTL_BRIDGE`, `LUXCTL_KEY`, `LUXCTL_CA_CERT`) → config file. If both bridge and key come from flags or env, no config file is read.

```yaml
# ~/.openhue/config.yaml
bridge: 192.168.1.100
key: your-hue-application-key
# ca_cert: ~/.openhue/bridge-cert.pem
```

Hue bridges serve a self-signed certificate, so verification is off unless `--ca-cert` is supplied. With a pinned certificate the chain is verified but hostname checking stays off, because the bridge certificate's common name is the bridge ID rather than its IP.

## Performance notes

luxctl fetches only the resource collections a command needs. Devices, lights, and rooms are always fetched because refs and room membership depend on them. The scene collection — usually the largest on a bridge, since every scene embeds one action per member light — is fetched only when the command touches scenes: `get scene`, `set scene`, `get room --with-scenes`, or `get room --detail`. `grouped_light` is fetched only for room reads.

Argument-only validation (missing selector, `--raw` without a selector) runs before any connection is opened, so those errors cost no round trip.

## Migrating from 0.2.x

`schema.version` went 1 → 2 in luxctl 0.3.0. If you encounter a caller written against the old contract:

- `raw` is gone from list responses; use `--raw` with a selector.
- Responses key on `ref`, not `id`. The uuid is `uuid` under `--detail`; `--id` still accepts uuids as a selector.
- Listing requires `--all` or a filter — bare `get light` now returns `selector_required`.
- Write responses report outcomes, not resource state. `bridge_response` and per-target `writes` moved behind `--verbose`.
- `set_room` no longer emits duplicate `target_lights` and `targets` arrays; exclusions appear under `excluded`.
- `--room-id` and `--room-name` collapsed into `--room`, which accepts a ref, name, or uuid.
- List results always carry `count`, `total`, and `truncated`.

## Introspection

```bash
luxctl schema         # full machine-readable schema, including identifiers and budget contract
luxctl list-actions   # actions and their parameters
luxctl --help         # help tree, also JSON
```

`luxctl schema` is the authority if this document and the binary ever disagree.
