#!/usr/bin/env python3
"""Validate restart recovery from CLI stderr without discarding diagnostics."""
import argparse
import json
from pathlib import Path

# SpeexDSP emits this diagnostic on each preprocessor initialization. Accept
# only this known line; malformed JSON and other diagnostics must still fail.
SPEEX_VAD_WARNING = "warning: The VAD has been replaced by a hack pending a complete rewrite"


def verify_recovery(text, path="speaker"):
    events, diagnostics = [], []
    for number, line in enumerate(text.splitlines(), 1):
        if line == SPEEX_VAD_WARNING:
            diagnostics.append({"line": number, "text": line})
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {number}: unexpected diagnostic or malformed JSON") from exc
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError(f"line {number}: invalid event object")
        if event["event"] == "error":
            raise ValueError(f"line {number}: CLI error: {event}")
        events.append(event)

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    restarts = [i for i, e in enumerate(events) if e["event"] == "restart" and e.get("path") == path]
    require(restarts, f"no restart for {path}")
    restart = restarts[-1]
    require(any(e["event"] == "started" and e.get("path") == path for e in events[:restart]),
            "no initial start before restart")
    starts = [i for i, e in enumerate(events) if i > restart and e["event"] == "started" and e.get("path") == path]
    require(starts, "no start after final restart")
    start = starts[-1]
    require(events[-1]["event"] == "stopped", "CLI did not stop cleanly")
    stats = []
    for event in events[start + 1:]:
        if event["event"] != "stats":
            continue
        require(isinstance(event.get("paths"), list), "invalid stats paths")
        matching = [p for p in event["paths"] if isinstance(p, dict) and p.get("path") == path]
        require(len(matching) == 1, "missing or duplicate recovered path stats")
        sample = matching[0]
        require(sample.get("primed") == 1, "recovered path is not primed")
        for key in ("pushed", "pulled"):
            require(type(sample.get(key)) is int and sample[key] > 0, f"invalid {key} counter")
        require(sample.get("underruns") == 0 and sample.get("overflows") == 0,
                "recovered path has underruns or overflows")
        stats.append(sample)
    require(len(stats) >= 2, "need at least two stats samples after final restart/start")
    for previous, current in zip(stats, stats[1:]):
        require(all(current[key] > previous[key] for key in ("pushed", "pulled")),
                "recovered callbacks did not advance")
    return {"recovered_after_coreaudiod_restart": True, "path": path,
            "restart_count": len(restarts), "post_restart_stats": len(stats),
            "first_counters": {k: stats[0][k] for k in ("pushed", "pulled")},
            "last_counters": {k: stats[-1][k] for k in ("pushed", "pulled")},
            "diagnostics": diagnostics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = verify_recovery(args.log.read_text())
    except ValueError as exc:
        parser.exit(1, f"CLI recovery validation failed: {exc}\n")
    rendered = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
