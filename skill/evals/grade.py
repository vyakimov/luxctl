#!/usr/bin/env python3
"""Grade luxctl skill eval runs against the mock bridge request log.

Assertions are checked programmatically rather than by eye: the bridge log is
ground truth for what a run actually did, and commands.txt is ground truth for
how it asked. Writes grading.json into each run directory.

Usage:
    python3 grade.py <iteration-dir>
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

# Bedroom is room index 2 in mock_bridge.build_inventory; "Night light" is the
# fourth fixture light in that room, so n = 2*10 + 3 = 23.
NIGHT_LIGHT_ID = str(uuid.UUID(int=23 + 3000))
TOTAL_LIGHTS = 30
# Bedroom fixture mirek values run 300-420 (mean ~353). Anything at or above
# this is unambiguously "warmer" than where the room started.
WARM_MIREK_FLOOR = 366


def load(run: Path) -> dict:
    reqs = []
    log = run / "requests.jsonl"
    if log.exists():
        reqs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    cmds_file = run / "outputs" / "commands.txt"
    cmds = [c.strip() for c in cmds_file.read_text().splitlines() if c.strip()] if cmds_file.exists() else []
    answer_file = run / "outputs" / "answer.md"
    answer = answer_file.read_text() if answer_file.exists() else ""
    return {
        "requests": reqs,
        "gets": [r for r in reqs if r["method"] == "GET"],
        "puts": [r for r in reqs if r["method"] == "PUT"],
        "commands": cmds,
        "luxctl_commands": [c for c in cmds if "luxctl" in c],
        "answer": answer,
    }


def light_puts(data: dict) -> list[dict]:
    return [p for p in data["puts"] if "/resource/light/" in p["path"]]


def put_ids(data: dict) -> set[str]:
    return {p["path"].rstrip("/").split("/")[-1] for p in light_puts(data)}


def is_full_list_read(cmd: str) -> bool:
    """A full inventory pull, as opposed to a cheap count or a narrowed list.

    `get light --all --count` returns about sixty bytes and `--on`/`--room`
    narrow at the source, so neither is the failure mode being tested here.
    """
    if not re.search(r"get\s+(light|room|scene)\b.*--all", cmd):
        return False
    return not re.search(r"--count|--on\s|--room\s|--limit\s", cmd)


def grade_turn_off(data: dict) -> list[dict]:
    off_ids = {
        p["path"].rstrip("/").split("/")[-1]
        for p in light_puts(data)
        if (p.get("body") or {}).get("on", {}).get("on") is False
    }
    cmds = " ; ".join(data["luxctl_commands"])
    # Only reads that precede the first write can have informed it.
    first_write = next((i for i, c in enumerate(data["luxctl_commands"]) if re.search(r"\bset\s", c)), len(data["luxctl_commands"]))
    read_all = any(is_full_list_read(c) for c in data["luxctl_commands"][:first_write])
    used_all_write = bool(re.search(r"set\s+(light|room)\b[^;]*--all", cmds))
    return [
        {"text": "All 30 lights end up off", "passed": len(off_ids) == TOTAL_LIGHTS,
         "evidence": f"{len(off_ids)} distinct lights received on:false"},
        {"text": "Did not run a full inventory read (`get light --all`) before writing",
         "passed": not read_all, "evidence": f"read_all={read_all}"},
        {"text": "Used an --all bulk primitive rather than enumerating lights",
         "passed": used_all_write, "evidence": f"commands: {cmds[:160]}"},
        {"text": "Used at most 2 luxctl invocations",
         "passed": len(data["luxctl_commands"]) <= 2,
         "evidence": f"{len(data['luxctl_commands'])} luxctl invocations"},
        {"text": "Reported the outcome in plain language, including how many lights",
         "passed": bool(data["answer"].strip()) and bool(re.search(r"\b30\b", data["answer"])),
         "evidence": f"answer {len(data['answer'])} chars, mentions 30: {bool(re.search(r'[b]30[b]', data['answer']))}"},
    ]


def grade_which_on(data: dict) -> list[dict]:
    cmds = " ; ".join(data["luxctl_commands"])
    used_filter = bool(re.search(r"get\s+light\b[^;]*--on\s+true", cmds))
    named = sum(1 for name in ("Ceiling", "Floor lamp", "Desk lamp", "Pantry", "Vanity") if name in data["answer"])
    return [
        {"text": "Used the --on filter rather than listing every light",
         "passed": used_filter, "evidence": f"commands: {cmds[:160]}"},
        {"text": "Did not request the raw view", "passed": "--raw" not in cmds,
         "evidence": f"--raw present: {'--raw' in cmds}"},
        {"text": "Named the lights that are on using human-readable names",
         "passed": named >= 3, "evidence": f"{named}/5 sampled fixture names present in answer"},
        {"text": "Made no state-changing PUT requests", "passed": len(data["puts"]) == 0,
         "evidence": f"{len(data['puts'])} PUTs"},
        {"text": "Reported a light count consistent with the bridge (24 on)",
         "passed": "24" in data["answer"], "evidence": f"answer mentions 24: {'24' in data['answer']}"},
    ]


def grade_dim_room(data: dict) -> list[dict]:
    cmds = " ; ".join(data["luxctl_commands"])
    # A --dry-run preview before the real write is good practice, not a split
    # change, so only committing writes count toward this.
    set_calls = sum(
        1 for c in data["luxctl_commands"]
        if re.search(r"\bset\s+(?:light|room|scene)\b", c) and "--dry-run" not in c
    )
    bodies = [(p.get("body") or {}) for p in light_puts(data)]
    brightness = [b.get("dimming", {}).get("brightness") for b in bodies if "dimming" in b]
    mireks = [b.get("color_temperature", {}).get("mirek") for b in bodies if "color_temperature" in b]
    return [
        {"text": "Targeted the bedroom as a room rather than enumerating its lights",
         "passed": bool(re.search(r"set\s+room\b", cmds)) and "bedroom" in cmds.lower(),
         "evidence": f"commands: {cmds[:160]}"},
        {"text": "Used --exclude to spare the night light",
         "passed": "--exclude" in cmds, "evidence": f"--exclude present: {'--exclude' in cmds}"},
        {"text": "The night light received no PUT",
         "passed": NIGHT_LIGHT_ID not in put_ids(data),
         "evidence": f"night light written: {NIGHT_LIGHT_ID in put_ids(data)}; {len(put_ids(data))} lights written"},
        {"text": "Set brightness to 30",
         "passed": bool(brightness) and all(b == 30 for b in brightness),
         "evidence": f"brightness values: {sorted(set(brightness))}"},
        {"text": f"Raised the mirek (warmer, >= {WARM_MIREK_FLOOR})",
         "passed": bool(mireks) and all(m is not None and m >= WARM_MIREK_FLOOR for m in mireks),
         "evidence": f"mirek values: {sorted(set(m for m in mireks if m is not None))}"},
        {"text": "Combined the changes into a single set call",
         "passed": set_calls == 1, "evidence": f"{set_calls} set calls"},
    ]


GRADERS = {
    "turn-off-all-lights": grade_turn_off,
    "which-lights-are-on": grade_which_on,
    "dim-room-with-exclusion": grade_dim_room,
}


def main() -> None:
    iteration = Path(sys.argv[1])
    overall = []
    for eval_dir in sorted(iteration.iterdir()):
        if not eval_dir.is_dir() or eval_dir.name not in GRADERS:
            continue
        for config in ("with_skill", "without_skill"):
            run = eval_dir / config
            if not run.exists():
                continue
            data = load(run)
            expectations = GRADERS[eval_dir.name](data)
            passed = sum(1 for e in expectations if e["passed"])
            result = {
                "eval_name": eval_dir.name,
                "config": config,
                "expectations": expectations,
                "pass_rate": passed / len(expectations),
                "passed": passed,
                "total": len(expectations),
                "request_count": len(data["requests"]),
                "put_count": len(data["puts"]),
                "luxctl_invocations": len(data["luxctl_commands"]),
            }
            (run / "grading.json").write_text(json.dumps(result, indent=2))
            overall.append(result)
            mark = "PASS" if passed == len(expectations) else "FAIL"
            print(f"[{mark}] {eval_dir.name:26s} {config:14s} {passed}/{len(expectations)}"
                  f"  ({len(data['luxctl_commands'])} cmds, {len(data['requests'])} reqs)")
            for exp in expectations:
                if not exp["passed"]:
                    print(f"         miss: {exp['text']}  -- {exp['evidence']}")

    for config in ("with_skill", "without_skill"):
        rows = [r for r in overall if r["config"] == config]
        if rows:
            rate = sum(r["pass_rate"] for r in rows) / len(rows)
            print(f"\n{config:14s} mean pass rate {rate:.0%}"
                  f"   mean luxctl invocations {sum(r['luxctl_invocations'] for r in rows)/len(rows):.1f}")


if __name__ == "__main__":
    main()
