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
- `knowledge ...` → **KNOWLEDGE** (static curated knowledge base)
- `mission ...` → **MISSION** (finite queue of objectives to validate, 1/tick)

## Memory model (two complementary layers)
- **Knowledge base (local, always on)**: static curated facts that must NOT be
  lost — how to start the stack, how to use NATS, gotchas, conventions. Stored as
  markdown files on disk. Both the human AND Claude fill it. The tick reads it so
  the loop "remembers". See KNOWLEDGE.
- **claude-mem (MCP, optional)**: cross-session dynamic memory via the claude-mem
  MCP tools. Enabled at SETUP (asks yes/no). When on, the tick also
  searches/saves memories there. Augments the local knowledge base; does NOT
  replace it.

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
   - **claude-mem (optional)**: ask the user "Use claude-mem (MCP) for
     cross-session memory? [y/N]". If yes, pass `--use-claude-mem` to `init` and
     the tick will search/save memories there too. If the claude-mem MCP isn't
     available, fall back to local knowledge only.
   > Unit = **tokens measured from the transcript** (input+output+cache_creation,
   > summed across all session `.jsonl`). Tune the caps to your plan. cache_read
   > is ignored by default (inflated by re-read context).
   - **Seed the knowledge base**: offer to capture key local know-how now
     (how to start the stack / run tests / use NATS, etc) via
     `knowledge add` — so the loop has it from tick 1.
   - **Missions (optional)**: ask if the user has a list of objectives to validate
     (a checklist of flows/scenarios/bugs). If yes, add each via `mission add`, and
     ask whether to **stop when the queue drains** (`--stop-when-missions-done` on
     init) or fall back to regression after.
2. **Clean tree + branch**: confirm `git status` clean; create the branch from
   the default branch: `git switch <default> && git switch -c autopilot/<YYYY-MM-DD-HHMM>`.
3. **Init state**:
   `python3 $ENGINE init --daily <D> --weekly <W> --cron "<CRON>" --branch <NAME> --target "<cmd1>" --target "<cmd2>" [--use-claude-mem]`
4. **Arm the cron**: `CronCreate(cron="<CRON>", prompt="/autopilot tick", recurring=true)`.
   Take the returned `job id` and store it: `python3 $ENGINE set-cron --cron-job-id <ID>`.
   > A recurring cron auto-expires in 7 days. It dies if the session dies — see RESUME.
5. **Run the first tick now** (follow TICK).
6. **Tell the user**: branch created, cron armed (expires 7d), how to stop
   (`/autopilot stop`), and the context-burn caveat.

---

## TICK  (what the cron fires — self-contained)

0. **Recall**: `python3 $ENGINE knowledge list` (and `knowledge get <id>` for the
   relevant ones) so you remember how to start the stack / run things / known
   gotchas. If `config.use_claude_mem` is true, also do a claude-mem
   `memory_search` for the area you're about to touch.
1. **Gate**: `python3 $ENGINE gate`
   → read the JSON `{verdict, reason, daily_pct_used, weekly_pct_used, branch, cron_job_id, targets, ticks, ...}`.
2. If `verdict == "stop"`:
   - `CronDelete(<cron_job_id from gate>)`.
   - Report: reason (daily or weekly budget) + summary (ticks, bugs found/fixed).
   - **DONE. Do nothing else this tick.**
3. If `verdict == "continue"`:
   1. `git switch <branch from state>` (ensure on the autopilot branch; never on the default branch).
   2. **Pick the work for this tick — missions take priority over regression:**
      - `python3 $ENGINE mission next` → if it returns a `mission` (non-null),
        **that is the tick's objective**. Do exactly what the `goal` says (validate
        a flow, test a scenario, reproduce/verify a bug), using the knowledge base
        + targets as needed.
        - Objective met / validated OK → `python3 $ENGINE mission done <id> --note "<result>"`.
        - Found a bug → fix root cause (`systematic-debugging`) → green → commit →
          `git push origin <branch>` → `python3 $ENGINE mission done <id> --note "fixed: <summary>"`
          (or `mission fail <id> --note "<why>"` if it couldn't be resolved this tick).
      - If `mission next` returns `null` (empty queue):
        - if `config.stop_when_missions_done` is true → run **STOP** (queue drained).
        - else → fall back to **regression**: pick `targets[ ticks % len(targets) ]`, run it.
   3. **Run / act on the chosen work.**
      - **Passed / validated** → `python3 $ENGINE log --note "<what> ok"` → **end tick**.
      - **Failed / bug** → `systematic-debugging` → fix → re-run until **green** →
        commit (repo style) → `git push origin <branch>` →
        `python3 $ENGINE log --note "fix: <summary>" --bug --fixed --commit <sha>`.
   4. **Capture durable lessons**: if you learned something that must not be lost
      (a setup step, a gotcha, a non-obvious fix), save it:
      `python3 $ENGINE knowledge add --by claude --title "..." --tags "..." --body "..."`.
      If `config.use_claude_mem`, also `memory_add` it to claude-mem.
   5. **1 fix per tick.** End.
4. **Do not re-arm the cron** (it's already recurring). The tick just executes.

---

## STOP
1. `python3 $ENGINE stop --reason "<why>"`.
2. `CronDelete(<cron_job_id from state>)`.
3. Confirm: stopped + summary (`python3 $ENGINE status`).

## STATUS
`python3 $ENGINE status` → summarize in 1 paragraph: status, branch, ticks, bugs
found/fixed, % of daily and weekly budget used. Also run `python3 $ENGINE
mission list` and report the mission queue (pending/done/failed).

## MISSION  (`/autopilot mission ...`)

A **finite queue of objectives** to validate, consumed **one per tick** (missions
take priority over the regression targets). Use this to drive the autonomous runs
toward specific goals ("validate the export returns a zip", "test copilot first
message", "reproduce bug #123") instead of only re-running the test suite.

- `add`  → `python3 $ENGINE mission add --goal "<objective>"`
           (omit `--goal` to read MANY at once from stdin, one objective per line)
- `list` → `python3 $ENGINE mission list`   (counts + each item's status)
- `next` → `python3 $ENGINE mission next`   (the next pending objective, or null)
- `done` → `python3 $ENGINE mission done <id> --note "<result>"`
- `fail` → `python3 $ENGINE mission fail <id> --note "<why>"`
- `rm`   → `python3 $ENGINE mission rm <id>`
- `clear`→ `python3 $ENGINE mission clear [--done-only]`

When the user says "run these objectives", "validate this list", "add a mission",
or pastes a checklist, turn each item into a mission. The tick picks the next
pending one, does it, and marks it done/failed. When the queue is empty the loop
falls back to regression targets — unless `config.stop_when_missions_done` is set
(then it stops once drained).

## KNOWLEDGE  (`/autopilot knowledge ...`)

Static curated knowledge that must not be lost — usable by the human AND by you
(Claude) so future ticks remember. Backed by markdown files on disk.

- `list` → `python3 $ENGINE knowledge list`
- `add`  → `python3 $ENGINE knowledge add --title "..." [--tags "a,b"] [--by user|claude] --body "..."`
           (omit `--body` or pass `--body -` to read from stdin — good for long text)
- `get`  → `python3 $ENGINE knowledge get <id>`
- `search` → `python3 $ENGINE knowledge search "<query>"`
- `rm`   → `python3 $ENGINE knowledge rm <id>`

When the user says "remember that ...", "save this", or "/autopilot knowledge add",
write an entry. When YOU learn something durable during a tick, save it with
`--by claude`. Examples worth saving: how to start the stack with docker compose,
how to use NATS, DB names/ports, test commands, recurring gotchas.

## RESUME (session died and was reopened)
The state persists, but the cron dies with the session. On reopen, to continue:
run `/autopilot tick` (the gate decides if budget remains); if `continue` and the
cron no longer exists, **re-arm** it (SETUP step 4) and update the `cron-job-id`.

---

## Cost note (caveat)
This mode consumes session context linearly and burns fast on capped plans. The
budget guards (90%/80%) cut before the limit, but check `status` periodically and
don't hesitate to `stop` if burn looks strange.
