#!/usr/bin/env python3
"""
autopilot.py — deterministic engine for Claude Code "autopilot" mode.

Handles: measuring tokens spent (reading the session .jsonl transcripts),
controlling the daily/weekly budget with rollover, and deciding continue/stop.
ALL budget arithmetic lives HERE (not in the model) so it's reliable.

Subcommands:
  init   --daily N --weekly M [--daily-pct .9 --weekly-pct .8 --cron "0 * * * *"
                              --branch NAME --max-fixes-per-tick 1
                              --target "go test ./..." --target "npm test"
                              --state PATH]
  gate   [--state PATH]   -> prints JSON {verdict, reason, ...} and updates state
  log    --note "..." [--bug] [--fixed] [--commit SHA] [--state PATH]
  status [--state PATH]
  stop   [--reason "..."] [--state PATH]
  tokens                  -> just prints the token total measured now
  set-cron --cron-job-id ID [--state PATH]

Token metric (default): input + output + cache_creation per message, summed over
ALL .jsonl files in the project's transcript dir (survives session death/resume).
cache_read is ignored by default (cheap, and inflated by re-read context each
turn). Use --include-cache-read to add it.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

DEFAULT_STATE = os.path.join(os.getcwd(), ".claude", "autopilot", "state.json")


def transcript_dir() -> str:
    """Transcript dir for the current cwd's session.

    Claude Code encodes the cwd replacing '/' and '.' with '-'.
    e.g. /Users/x/proj/.claude/wt -> -Users-x-proj--claude-wt
    """
    cwd = os.getcwd()
    enc = re.sub(r"[/.]", "-", cwd)
    return os.path.expanduser(os.path.join("~/.claude/projects", enc))


def measure_tokens(include_cache_read: bool = False) -> int:
    """Sum usage tokens across all .jsonl files in the transcript dir."""
    d = transcript_dir()
    total = 0
    for path in glob.glob(os.path.join(d, "*.jsonl")):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    usage = None
                    if isinstance(obj, dict):
                        msg = obj.get("message")
                        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                            usage = msg["usage"]
                        elif isinstance(obj.get("usage"), dict):
                            usage = obj["usage"]
                    if not usage:
                        continue
                    total += int(usage.get("input_tokens", 0) or 0)
                    total += int(usage.get("output_tokens", 0) or 0)
                    total += int(usage.get("cache_creation_input_tokens", 0) or 0)
                    if include_cache_read:
                        total += int(usage.get("cache_read_input_tokens", 0) or 0)
        except FileNotFoundError:
            continue
    return total


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_day(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d")


def iso_week(d: dt.datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def load_state(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def cmd_init(args) -> int:
    total = measure_tokens(args.include_cache_read)
    now = now_utc()
    state = {
        "status": "running",
        "stop_reason": "",
        "branch": args.branch,
        "created_at": now.isoformat(),
        "config": {
            "daily_budget_tokens": args.daily,
            "weekly_budget_tokens": args.weekly,
            "daily_stop_pct": args.daily_pct,
            "weekly_stop_pct": args.weekly_pct,
            "schedule_cron": args.cron,
            "max_fixes_per_tick": args.max_fixes_per_tick,
            "include_cache_read": bool(args.include_cache_read),
            "targets": args.target or [],
        },
        "counters": {"ticks": 0, "bugs_found": 0, "bugs_fixed": 0},
        "budget": {
            "day": iso_day(now),
            "week": iso_week(now),
            "tokens_at_day_start": total,
            "tokens_at_week_start": total,
            "last_total": total,
            "last_daily_used": 0,
            "last_weekly_used": 0,
        },
        "cron_job_id": args.cron_job_id or "",
        "log": [],
    }
    save_state(args.state, state)
    print(json.dumps({"ok": True, "state": args.state, "branch": args.branch,
                      "tokens_now": total, "targets": state["config"]["targets"]},
                     ensure_ascii=False))
    return 0


def cmd_gate(args) -> int:
    state = load_state(args.state)
    cfg = state["config"]
    b = state["budget"]
    now = now_utc()
    total = measure_tokens(cfg.get("include_cache_read", False))

    # Daily/weekly rollover: new day/week -> checkpoint = current total.
    if iso_day(now) != b.get("day"):
        b["day"] = iso_day(now)
        b["tokens_at_day_start"] = total
    if iso_week(now) != b.get("week"):
        b["week"] = iso_week(now)
        b["tokens_at_week_start"] = total

    daily_used = max(0, total - b["tokens_at_day_start"])
    weekly_used = max(0, total - b["tokens_at_week_start"])
    daily_budget = cfg["daily_budget_tokens"]
    weekly_budget = cfg["weekly_budget_tokens"]
    daily_cap = daily_budget * cfg["daily_stop_pct"]
    weekly_cap = weekly_budget * cfg["weekly_stop_pct"]

    b["last_total"] = total
    b["last_daily_used"] = daily_used
    b["last_weekly_used"] = weekly_used

    verdict, reason = "continue", ""
    if state.get("status") != "running":
        verdict, reason = "stop", state.get("stop_reason") or "status != running"
    elif weekly_used >= weekly_cap:
        verdict = "stop"
        reason = f"weekly {weekly_used} >= {int(weekly_cap)} ({int(cfg['weekly_stop_pct']*100)}% of {weekly_budget})"
    elif daily_used >= daily_cap:
        verdict = "stop"
        reason = f"daily {daily_used} >= {int(daily_cap)} ({int(cfg['daily_stop_pct']*100)}% of {daily_budget})"

    if verdict == "stop" and state.get("status") == "running":
        state["status"] = "stopped"
        state["stop_reason"] = reason

    save_state(args.state, state)
    out = {
        "verdict": verdict,
        "reason": reason,
        "daily_used": daily_used,
        "daily_budget": daily_budget,
        "daily_cap": int(daily_cap),
        "daily_pct_used": round(daily_used / daily_budget * 100, 1) if daily_budget else 0,
        "weekly_used": weekly_used,
        "weekly_budget": weekly_budget,
        "weekly_cap": int(weekly_cap),
        "weekly_pct_used": round(weekly_used / weekly_budget * 100, 1) if weekly_budget else 0,
        "branch": state.get("branch"),
        "cron_job_id": state.get("cron_job_id"),
        "ticks": state["counters"]["ticks"],
        "targets": cfg.get("targets", []),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_log(args) -> int:
    state = load_state(args.state)
    state["counters"]["ticks"] += 1
    if args.bug:
        state["counters"]["bugs_found"] += 1
    if args.fixed:
        state["counters"]["bugs_fixed"] += 1
    state.setdefault("log", []).append({
        "tick": state["counters"]["ticks"],
        "ts": now_utc().isoformat(),
        "note": args.note,
        "bug": bool(args.bug),
        "fixed": bool(args.fixed),
        "commit": args.commit or "",
        "daily_used": state["budget"].get("last_daily_used"),
        "weekly_used": state["budget"].get("last_weekly_used"),
    })
    save_state(args.state, state)
    print(json.dumps({"ok": True, "ticks": state["counters"]["ticks"]}, ensure_ascii=False))
    return 0


def cmd_status(args) -> int:
    print(json.dumps(load_state(args.state), indent=2, ensure_ascii=False))
    return 0


def cmd_stop(args) -> int:
    state = load_state(args.state)
    state["status"] = "stopped"
    state["stop_reason"] = args.reason or "manual"
    save_state(args.state, state)
    print(json.dumps({"ok": True, "status": "stopped", "reason": state["stop_reason"],
                      "cron_job_id": state.get("cron_job_id")}, ensure_ascii=False))
    return 0


def cmd_tokens(args) -> int:
    print(measure_tokens(args.include_cache_read))
    return 0


def cmd_set_cron(args) -> int:
    state = load_state(args.state)
    state["cron_job_id"] = args.cron_job_id
    save_state(args.state, state)
    print(json.dumps({"ok": True, "cron_job_id": args.cron_job_id}, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="autopilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--daily", type=int, required=True)
    pi.add_argument("--weekly", type=int, required=True)
    pi.add_argument("--daily-pct", type=float, default=0.90)
    pi.add_argument("--weekly-pct", type=float, default=0.80)
    pi.add_argument("--cron", default="0 * * * *")
    pi.add_argument("--branch", required=True)
    pi.add_argument("--max-fixes-per-tick", type=int, default=1)
    pi.add_argument("--target", action="append", default=[])
    pi.add_argument("--cron-job-id", default="")
    pi.add_argument("--include-cache-read", action="store_true")
    pi.add_argument("--state", default=DEFAULT_STATE)
    pi.set_defaults(func=cmd_init)

    pg = sub.add_parser("gate")
    pg.add_argument("--state", default=DEFAULT_STATE)
    pg.set_defaults(func=cmd_gate)

    pl = sub.add_parser("log")
    pl.add_argument("--note", required=True)
    pl.add_argument("--bug", action="store_true")
    pl.add_argument("--fixed", action="store_true")
    pl.add_argument("--commit", default="")
    pl.add_argument("--state", default=DEFAULT_STATE)
    pl.set_defaults(func=cmd_log)

    ps = sub.add_parser("status")
    ps.add_argument("--state", default=DEFAULT_STATE)
    ps.set_defaults(func=cmd_status)

    pst = sub.add_parser("stop")
    pst.add_argument("--reason", default="manual")
    pst.add_argument("--state", default=DEFAULT_STATE)
    pst.set_defaults(func=cmd_stop)

    pt = sub.add_parser("tokens")
    pt.add_argument("--include-cache-read", action="store_true")
    pt.set_defaults(func=cmd_tokens)

    pc = sub.add_parser("set-cron")
    pc.add_argument("--cron-job-id", required=True)
    pc.add_argument("--state", default=DEFAULT_STATE)
    pc.set_defaults(func=cmd_set_cron)

    args = p.parse_args()
    if not hasattr(args, "include_cache_read"):
        args.include_cache_read = False
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
