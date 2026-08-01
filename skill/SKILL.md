---
name: luxctl
description: Control Philips Hue lights from the command line via luxctl — turning lights on or off, dimming, setting colour or colour temperature, recalling scenes, and checking which lights are currently on. Use this skill whenever the user asks about their lights, lamps, bulbs, rooms, or scenes in a home-automation context ("turn off the lights", "dim the bedroom", "make the kitchen warmer", "is anything still on downstairs", "set the living room to Relax"), even if they never say "Hue" or "luxctl". It covers the response format, how to address lights, and how to avoid pulling large inventories into context.
---

# luxctl

A JSON-only Hue CLI. Every invocation prints exactly one JSON object on stdout — success, error, and `--help` alike. Exit code is 0 on success, 1 on error, and `ok` mirrors it.

```json
{"ok": true, "action": "set_room", "result": { ... }, "meta": { ... }}
```

## The four things that matter most

**1. Prefer a write over a read.** Most requests are mutations, and mutations do not need an inventory first. "Turn off all the lights" is one command, not a read followed by a fan-out:

```bash
luxctl set light --all --on false
```

Reading the light list to figure out what to turn off is the single most common mistake. The bridge applies `on: false` to an already-off light harmlessly, so there is nothing to filter for.

**2. Address things by ref.** A ref is a readable slug derived from the resource name: `desk-lamp`, `kitchen-ceiling`, `bedroom`. Colliding names are qualified by room (`office/lamp` vs `bedroom/lamp`). Use `--ref` wherever you can.

Refs come from names and are recomputed every call, so a renamed light gets a new ref. They are identifiers for the current conversation, not durable keys — never cache one across a session.

**3. Check `truncated` before trusting a list.** Every list response carries `count`, `total`, and `truncated`:

```json
{"lights": [...], "count": 25, "total": 40, "truncated": true, "hint": "..."}
```

If `truncated` is `true` the list is incomplete. Narrow the query — do not act as though you have seen everything. Acting on a truncated list is how you silently miss half the lights.

**4. Listing everything is deliberate.** `luxctl get light` on its own fails with `selector_required`. Pass `--all`, a filter, or a selector. This is a guardrail, not a bug: it stops you pulling the whole bridge when you wanted one lamp.

## Common tasks

### Turn things on or off

```bash
luxctl set light --all --on false            # every light on the bridge
luxctl set room --all --on false             # every light that belongs to a room
luxctl set room --ref bedroom --on true      # one room
luxctl set light --ref desk-lamp --on true   # one light
luxctl set light --ref left-sconce --ref right-sconce --on true   # several
```

`set light --all` is the broadest hammer — it includes lights not assigned to any room. `set room --all` only reaches lights that belong to a room. When the user says "everything", prefer `set light --all`.

### Dim, warm, or colour

Brightness is 0–100. Mirek (colour temperature) is 153–500, lower being cooler. Fields combine in one call:

```bash
luxctl set room --ref bedroom --brightness 30
luxctl set room --ref living-room --brightness 60 --mirek 400      # warm and dim
luxctl set light --ref desk-lamp --hex "#ff6600"
luxctl set light --ref desk-lamp --hex warmwhite
```

`--hex` accepts `#RRGGBB` or a colour name: `black white red green blue yellow orange purple pink cyan magenta warmwhite coolwhite`. "Warmer" means a *higher* mirek; "cooler"/"whiter" means lower.

### Exclude a light from a bulk change

`--exclude` lives on `set room` only, and is repeatable:

```bash
luxctl set room --ref bedroom --on false --exclude night-light
luxctl set room --all --on false --exclude night-light      # every room, sparing one light
```

Exclusions resolve against whatever the scope is, so `--all --exclude` works the same way as a single room. There is no `--exclude` on `set light`, so "everything except X" has to go through `set room --all` — which reaches only lights assigned to a room. If the user has room-less lights, say so rather than quietly missing them.

### Find out what's on

```bash
luxctl get light --on true      # only the lights that are on
luxctl get light --on false     # only the ones that are off
luxctl get light --room kitchen # lights in one room
luxctl get light --all --count  # just the number
```

Filter at the source rather than fetching everything and filtering yourself. `--on` and `--room` both satisfy the "deliberate listing" rule, so they need no `--all`.

A compact light looks like this — five fields, nothing else:

```json
{"ref": "desk-lamp", "name": "Desk lamp", "on": true, "brightness": 62, "room": "office"}
```

### Rooms and scenes

```bash
luxctl get room --all                          # room names and on-state
luxctl get room --ref bedroom --with-lights    # one room and its members
luxctl get scene --room bedroom                # scenes available in a room
luxctl set scene --ref bedroom/relax           # recall one
luxctl set scene --name Relax --room bedroom   # same thing by name
```

Rooms do not include their lights or scenes unless you ask with `--with-lights` / `--with-scenes`. Scene names repeat across rooms constantly ("Relax" exists in most), so scene refs are usually room-qualified — pass `--room` or use the qualified ref.

### Preview before committing

```bash
luxctl set room --ref bedroom --on false --dry-run
```

Returns the exact payload that would be sent, and touches nothing. Useful when the user's intent is ambiguous and you want to show them what you're about to do.

## Reading responses

### A successful write

```json
{"ok": true, "action": "set_room", "result": {
  "room": {"ref": "bedroom", "name": "Bedroom"},
  "applied": {"on": {"on": false}},
  "summary": {"target_count": 4, "success_count": 4, "failure_count": 0, "dry_run": false}
}, "meta": {"bridge_ip": "...", "api_version": "v2", "dry_run": false, "view": "compact"}}
```

Read `summary` to confirm what happened. `target_count` tells the user how many lights you touched — worth reporting back ("turned off 4 lights in the bedroom").

If anything failed, `failure_count` is non-zero and a `failures` array lists each failed target with a typed error. Failures always appear; you never need `--verbose` to see them.

### An error

```json
{"ok": false, "action": "set_light", "error": {
  "type": "resource_not_found",
  "message": "No light matched the ref",
  "details": {"ref": "desklamp", "hint": "Refs are derived from names; list them with `get light --all`."}
}}
```

Branch on `error.type`, not on the message. `details.hint` often tells you the recovery step directly.

The types you will actually hit:

| Type | What to do |
|---|---|
| `selector_required` | Add `--all`, a filter, or a selector. |
| `resource_not_found` | The ref/name is wrong. List the collection to find the real one. |
| `ambiguous_match` | A name matched several resources. `details.matches` gives you the refs — pick one and retry with `--ref`. |
| `invalid_arguments` | Bad flags or out-of-range values. Read the message. |
| `bridge_unreachable` / `timeout` | The bridge is offline or slow. Report it; do not retry in a loop. |
| `config_not_found` / `config_invalid` | Credentials aren't set up. Point the user at the README's pairing section. |

The full list is in `reference.md`.

## Working efficiently

- **Don't read before writing.** Writes take selectors directly. Only read when the user asked a question about state, or when you genuinely need to discover a name.
- **Start narrow.** `get light --on true` beats `get light --all` when you want what's on. `--count` beats a full list when you only need a number.
- **Stay compact.** The default view is deliberately small. Only reach for `--detail` when you need exact brightness, colour coordinates, or the bridge uuid — and `--raw` essentially never, since it requires a selector and returns the full bridge resource for debugging.
- **Avoid scene commands when you don't need scenes.** luxctl skips fetching the scene collection (the largest on most bridges) unless the command touches scenes, so scene-free calls are faster.

## Talking to the user

Report what changed in their words, not the tool's. "Turned off all 12 lights" is better than pasting JSON. Use `name` for display and `ref` for subsequent commands — the user thinks in "Desk lamp", the CLI thinks in `desk-lamp`.

When a bulk change partially fails, say which lights failed and why rather than reporting blanket success.

## Reference

`reference.md` (next to this file) covers the rest: every flag for every subcommand, the JSON input mode for programmatic callers, the complete error table, ref derivation and collision rules, the response budget and `--max-bytes`, configuration and TLS, and migration notes from luxctl 0.2.x. Read it when you need a flag that isn't above, when you're driving luxctl from a script rather than a shell, or when a response shape surprises you.
