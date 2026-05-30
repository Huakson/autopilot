---
name: autopilot
description: >-
  Use when the user wants to run Claude Code autonomously for long stretches —
  test the app, find and fix bugs by itself, committing to an isolated branch,
  with a token-budget guard that STOPS at 90% of the daily cap and limits to 80%
  of the weekly cap. Triggers on "autopilot mode", "run by yourself testing and
  fixing", "autopilot setup/tick/stop/status", or when the autopilot cron fires
  the tick (`/autopilot tick`).
---

# Autopilot — autonomous test-and-fix loop

Keeps Claude Code running on its own: each **tick** (fired by cron) runs the
project's test targets, finds a bug, fixes it on an **isolated branch**,
commits/pushes, and **STOPS when the token budget is exhausted**. The on-disk
`state.json` is the source of truth — it survives session death (resume reopens).

Pattern: state file on disk + self-contained prompt the cron fires + dogfooding.
Extended with **budget guards** (90% daily / 80% weekly).

## Fixed paths
- **Engine (budget/measurement)**: `scripts/autopilot.py` (next to this skill).
  ALWAYS use it to measure tokens and decide continue/stop. **Never count tokens
  by hand.**
- **State**: `.claude/autopilot/state.json` (under the project cwd; gitignored).

> Resolve the engine path from where the skill is installed, e.g.
> `~/.claude/skills/autopilot/scripts/autopilot.py`. Examples below use `$ENGINE`.

## The skill argument decides the mode
- empty or `setup` → **SETUP**
- `tick` → **TICK** (this is what the cron fires)
- `stop` → **STOP**
- `status` → **STATUS**

---

## ⛔ SAFETY — hard rules

1. **NEVER** merge to the main/default branch. **NEVER** force-push. Only
   commit/push to the autopilot branch (the human merges later).
2. **1 fix per tick** (bounded cost). If the suite passes, log "no bug" and end
   the tick — don't keep hunting.
3. **Budget is HARD**: the `autopilot.py gate` verdict rules. `stop` means stop
   for real (CronDelete) — no "just one more".
4. Respect the project's conventions (commit style; no AI-attribution trailers
   if the repo forbids them; no `claude/` branch prefix if forbidden; follow any
   CLAUDE.md / AGENTS.md).
5. Anything weird (abnormal token burn, repeated error with no progress, dirty
   repo you didn't expect, infinite flaky test) → run `STOP` and report. Kill the
   session if burn looks strange (this mode eats context fast).
6. Never touch secrets / `.env` / production data. Local stack/DB only.

---

## SETUP

1. **Config** (ask or use defaults):
   - `daily_budget_tokens` (default `30000000`)
   - `weekly_budget_tokens` (default `150000000`)
   - `cron` (default `0 * * * *` = hourly)
   - **targets**: the commands/specs to run each tick (e.g. `go test ./...`,
     `npm test`, `pytest -q`, a Playwright spec, a key user flow). Ask the user
     or detect from the repo (package.json scripts, go.mod, pytest, etc).
   - Fixed by design: stop at **90%** of daily, cap at **80%** of weekly,
     **1 fix/tick**, integration via **isolated branch** (no merge).
   > Unit = **tokens measured from the transcript** (input+output+cache_creation,
   > summed across all session `.jsonl`). Tune the caps to your plan. cache_read
   > is ignored by default (inflated by re-read context).
2. **Clean tree + branch**: confirm `git status` clean; create the branch from
   the default branch: `git switch <default> && git switch -c autopilot/<YYYY-MM-DD-HHMM>`.
3. **Init state**:
   `python3 $ENGINE init --daily <D> --weekly <W> --cron "<CRON>" --branch <NAME> --target "<cmd1>" --target "<cmd2>"`
4. **Arm the cron**: `CronCreate(cron="<CRON>", prompt="/autopilot tick", recurring=true)`.
   Take the returned `job id` and store it: `python3 $ENGINE set-cron --cron-job-id <ID>`.
   > A recurring cron auto-expires in 7 days. It dies if the session dies — see RESUME.
5. **Run the first tick now** (follow TICK).
6. **Tell the user**: branch created, cron armed (expires 7d), how to stop
   (`/autopilot stop`), and the context-burn caveat.

---

## TICK  (what the cron fires — self-contained)

1. **Gate**: `python3 $ENGINE gate`
   → read the JSON `{verdict, reason, daily_pct_used, weekly_pct_used, branch, cron_job_id, targets, ticks, ...}`.
2. If `verdict == "stop"`:
   - `CronDelete(<cron_job_id from gate>)`.
   - Report: reason (daily or weekly budget) + summary (ticks, bugs found/fixed).
   - **DONE. Do nothing else this tick.**
3. If `verdict == "continue"`:
   1. `git switch <branch from state>` (ensure on the autopilot branch; never on the default branch).
   2. **Pick 1 target** by rotating: `targets[ ticks % len(targets) ]`.
   3. **Run** it.
      - **Passed** → `python3 $ENGINE log --note "<target> ok"` → **end tick**.
      - **Failed / bug** → use the `systematic-debugging` discipline → fix the root
        cause → re-run until **green** → commit (repo style) → `git push origin <branch>`
        → `python3 $ENGINE log --note "fix: <summary>" --bug --fixed --commit <sha>`.
   4. **1 fix per tick.** End.
4. **Do not re-arm the cron** (it's already recurring). The tick just executes.

---

## STOP
1. `python3 $ENGINE stop --reason "<why>"`.
2. `CronDelete(<cron_job_id from state>)`.
3. Confirm: stopped + summary (`python3 $ENGINE status`).

## STATUS
`python3 $ENGINE status` → summarize in 1 paragraph: status, branch, ticks, bugs
found/fixed, % of daily and weekly budget used.

## RESUME (session died and was reopened)
The state persists, but the cron dies with the session. On reopen, to continue:
run `/autopilot tick` (the gate decides if budget remains); if `continue` and the
cron no longer exists, **re-arm** it (SETUP step 4) and update the `cron-job-id`.

---

## Cost note (caveat)
This mode consumes session context linearly and burns fast on capped plans. The
budget guards (90%/80%) cut before the limit, but check `status` periodically and
don't hesitate to `stop` if burn looks strange.
