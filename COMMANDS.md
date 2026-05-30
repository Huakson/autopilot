# autopilot — Command Reference & Walkthrough

Complete step-by-step guide: every command, every flag, the full lifecycle, and
copy-paste recipes. For the high-level "what/why", see [README.md](README.md).

- Skill commands (`/autopilot ...`) — what you type in Claude Code.
- Engine commands (`python3 $ENGINE ...`) — the underlying CLI the skill drives;
  you can run them directly for debugging.

```bash
# used throughout this doc
ENGINE=~/.claude/skills/autopilot/scripts/autopilot.py
```

---

## 0. Mental model (30 seconds)

```
You ──/autopilot setup──▶ creates branch + state.json + arms cron + runs tick 1
                          │
cron (every N min) ──────┴──▶ /autopilot tick:
                                 1. recall knowledge (+ claude-mem)
                                 2. GATE: measure tokens → continue | stop
                                 3. if continue: take next MISSION (or rotate TARGET)
                                    → run it → bug? fix on branch → commit/push
                                 4. log + save state
                              STOPS at 90% daily / 80% weekly token budget
```

- **state.json** (`.claude/autopilot/state.json`) = source of truth, survives session death.
- **2 work sources:** `missions` (a finite to-do list, priority) and `targets`
  (regression commands that rotate forever, fallback).
- **2 memory layers:** `knowledge` (local markdown, always on) + `claude-mem` (MCP, optional).
- **NEVER merges** to your default branch. You merge the autopilot branch yourself.

---

## 1. Install

```bash
git clone https://github.com/Huakson/autopilot.git
cd autopilot
./install.sh          # → ~/.claude/skills/autopilot/
```
Requirements: Claude Code (with `CronCreate`), Python 3 (stdlib only), git.
Verify: open Claude Code, type `/autopilot` — it should appear in the skill list.

---

## 2. Lifecycle commands (`/autopilot <arg>`)

| You type | Mode | What happens |
|---|---|---|
| `/autopilot setup` (or empty) | SETUP | asks config, creates branch, arms cron, runs first tick |
| `/autopilot tick` | TICK | one iteration (this is what the cron fires) |
| `/autopilot status` | STATUS | budget %, ticks, bugs, mission queue |
| `/autopilot stop` | STOP | kills cron, marks stopped |
| `/autopilot knowledge ...` | KNOWLEDGE | curated facts (see §5) |
| `/autopilot mission ...` | MISSION | objective queue (see §6) |

---

## 3. Full walkthrough — first run

### Step 1 — Start setup
```
/autopilot setup
```
You'll be asked (defaults in brackets):
- **daily token cap** [30,000,000] → stops at 90% of it
- **weekly token cap** [150,000,000] → caps at 80% of it
- **cron** [`0 * * * *` = hourly] → how often a tick fires
- **targets** → regression commands to run each tick (detected from repo or asked)
- **claude-mem?** [N] → cross-session memory via MCP
- **knowledge seed?** → capture "how to start the stack / run tests" now
- **missions?** → paste a list of objectives now (optional)

### Step 2 — What setup does (under the hood)
```bash
git switch <default> && git switch -c autopilot/2026-05-30-1410   # isolated branch
python3 $ENGINE init --daily 30000000 --weekly 150000000 \
        --cron "0 * * * *" --branch autopilot/2026-05-30-1410 \
        --target "go test ./..." --target "npm test" [--use-claude-mem] \
        [--stop-when-missions-done]
# CronCreate(cron, prompt="/autopilot tick", recurring=true) → job id
python3 $ENGINE set-cron --cron-job-id <ID>
# then runs the first tick
```

### Step 3 — Let it run
The cron fires `/autopilot tick` every interval. You do nothing.

### Step 4 — Check in
```
/autopilot status
```

### Step 5 — Stop when done
```
/autopilot stop
```

---

## 4. Engine commands — lifecycle (for debugging)

```bash
# measure tokens spent right now (input+output+cache_creation across session .jsonl)
python3 $ENGINE tokens
python3 $ENGINE tokens --include-cache-read     # also count cache reads (inflated)

# initialize state (setup does this for you)
python3 $ENGINE init \
  --daily 30000000 --weekly 150000000 \
  --daily-pct 0.90 --weekly-pct 0.80 \
  --cron "0 * * * *" \
  --branch autopilot/2026-05-30-1410 \
  --target "go test ./..." --target "npm test" \
  --max-fixes-per-tick 1 \
  [--use-claude-mem] [--stop-when-missions-done] [--include-cache-read] \
  [--state ./.claude/autopilot/state.json]

# the budget decision (run by the tick; safe to run manually — it's idempotent-ish,
# it updates last_* counters and rolls over day/week)
python3 $ENGINE gate
#   → {"verdict":"continue|stop","reason":"...","daily_pct_used":..,"weekly_pct_used":..,
#      "branch":"...","cron_job_id":"...","ticks":N,"targets":[...]}

# record the outcome of a tick (advances tick counter)
python3 $ENGINE log --note "go test ok"
python3 $ENGINE log --note "fix: nil deref in archiver" --bug --fixed --commit abc1234

# store the cron job id into state (so STOP/tick know what to delete)
python3 $ENGINE set-cron --cron-job-id 064d51bd

# full state dump
python3 $ENGINE status

# stop (sets status=stopped; the skill also CronDeletes)
python3 $ENGINE stop --reason "manual"
```

### `init` flags
| Flag | Default | Meaning |
|---|---|---|
| `--daily N` | (required) | daily token budget |
| `--weekly N` | (required) | weekly token budget |
| `--daily-pct F` | `0.90` | stop fraction of daily |
| `--weekly-pct F` | `0.80` | stop fraction of weekly |
| `--cron "..."` | `0 * * * *` | tick schedule |
| `--branch NAME` | (required) | autopilot branch |
| `--target "cmd"` | — | repeatable; regression commands |
| `--max-fixes-per-tick N` | `1` | fixes allowed per tick |
| `--use-claude-mem` | off | also use claude-mem MCP |
| `--stop-when-missions-done` | off | stop when mission queue drains (else regression) |
| `--include-cache-read` | off | count cache_read tokens too |
| `--state PATH` | `./.claude/autopilot/state.json` | state file location |

---

## 5. Knowledge base (`/autopilot knowledge ...`)

Curated facts that must NOT be lost (how to start the stack, NATS usage, DB ports,
gotchas). Both you and Claude write it; each tick reads it. Markdown on disk:
`.claude/autopilot/knowledge/*.md`.

```bash
# add — inline body
python3 $ENGINE knowledge add --by user \
  --title "Start the stack" --tags "docker,stack" \
  --body "docker compose --env-file dev.env -f infra.yml -f compose.yml up -d"

# add — long body from stdin (omit --body or pass --body -)
cat NOTES.md | python3 $ENGINE knowledge add --by user --title "How to use NATS" --tags "nats"

python3 $ENGINE knowledge list             # all entries (id/title/tags/by/ts)
python3 $ENGINE knowledge get <id>         # full entry
python3 $ENGINE knowledge search "nats"    # match title/tags/body
python3 $ENGINE knowledge rm <id>          # delete
```
In chat: `/autopilot knowledge add ...`, or just say "remember that ...".
`--by user` (you) or `--by claude` (Claude saves a durable lesson during a tick).

---

## 6. Mission queue (`/autopilot mission ...`)

A **finite list of objectives**, validated **one per tick**, with **priority over
regression targets**. Drive the runs toward goals instead of only re-running suites.

```bash
# add one
python3 $ENGINE mission add --goal "Validate export returns a zip with xlsx"

# add many at once (one objective per line, from stdin)
python3 $ENGINE mission add <<'EOF'
Validate agent-filtered export reads only that agent's partitions
Test copilot first message renders without F5
Reproduce and fix bug #123
Validate cap-split on a large week
EOF

python3 $ENGINE mission list               # counts + each item's status
python3 $ENGINE mission next               # next pending objective (or null)
python3 $ENGINE mission done m3 --note "validated ok"
python3 $ENGINE mission fail m4 --note "couldn't resolve this tick"
python3 $ENGINE mission rm m5
python3 $ENGINE mission clear              # wipe all
python3 $ENGINE mission clear --done-only  # keep pending, drop done/failed
```

**How a tick consumes it:**
1. `mission next` → if a pending objective exists, that's the tick's focus → do it →
   `done`/`fail`.
2. empty queue → fall back to regression `targets[ ticks % len(targets) ]`.
3. with `--stop-when-missions-done`, an empty queue → STOP instead of regression.

Statuses: `pending` → `done` | `failed`. IDs are `m1, m2, …` (stable).

In chat: paste a checklist and say "run these objectives" — each line becomes a mission.

---

## 7. Budget — how the guard works

- **Metric:** `input + output + cache_creation` tokens, summed across every `.jsonl`
  in this project's transcript dir (`~/.claude/projects/<cwd-encoded>/`).
- **Baseline:** `init` records the current total; usage is measured as a **delta**
  from there, so absolute history doesn't matter.
- **Rollover:** `gate` resets the day checkpoint when the UTC day changes, and the
  week checkpoint when the ISO week changes.
- **Stop conditions (whichever first):**
  - `daily_used ≥ 90% × daily_budget`
  - `weekly_used ≥ 80% × weekly_budget`
- On stop: `gate` returns `{"verdict":"stop","reason":"..."}`, the tick CronDeletes
  and ends.

Check anytime: `python3 $ENGINE gate` → `daily_pct_used` / `weekly_pct_used`.

---

## 8. Resume after the session dies

The cron lives only in the session; `state.json` persists. On reopen:
```
/autopilot tick
```
- `gate` decides if budget remains.
- if `continue` and the cron is gone, re-arm it:
  `CronCreate(cron, "/autopilot tick", recurring=true)` → `python3 $ENGINE set-cron --cron-job-id <ID>`.

---

## 9. Recipes

**Run a specific checklist, then stop:**
```bash
python3 $ENGINE init --daily 10000000 --weekly 50000000 --cron "*/30 * * * *" \
  --branch autopilot/$(date -u +%Y-%m-%d-%H%M) --target "go test ./..." \
  --stop-when-missions-done
python3 $ENGINE mission add <<'EOF'
Validate X
Validate Y
EOF
# arm cron + first tick via /autopilot setup, or run /autopilot tick manually
```

**Conservative test of the mechanism (low budget, frequent ticks):**
```
/autopilot setup   # answer: 10M/50M, every 30 min, Go-only target, claude-mem N
```

**Teach Claude something permanent mid-run:**
```
/autopilot knowledge add --by user --title "DB reset" --tags "db" --body "docker exec postgres psql -U postgres -d mindchain_platform -c 'TRUNCATE messages CASCADE;'"
```

**See where it stands:**
```
/autopilot status
```

---

## 10. Safety recap (always enforced)

1. Never merges to the default branch; never force-pushes. Commits only to the
   autopilot branch — you merge.
2. 1 fix per tick (bounded cost).
3. Budget verdict is hard — `stop` means stop (CronDelete).
4. Follows the repo's conventions (commit style, CLAUDE.md/AGENTS.md).
5. Stops on anything weird (abnormal burn, repeated error, dirty repo).
6. Never touches secrets / `.env` / production — local stack/DB only.

---

## 11. State file shape (reference)

```json
{
  "status": "running | stopped",
  "stop_reason": "",
  "branch": "autopilot/2026-05-30-1410",
  "created_at": "ISO-8601",
  "config": {
    "daily_budget_tokens": 30000000,
    "weekly_budget_tokens": 150000000,
    "daily_stop_pct": 0.9,
    "weekly_stop_pct": 0.8,
    "schedule_cron": "0 * * * *",
    "max_fixes_per_tick": 1,
    "include_cache_read": false,
    "targets": ["go test ./...", "npm test"],
    "use_claude_mem": false,
    "stop_when_missions_done": false
  },
  "missions": [
    { "num": 1, "id": "m1", "goal": "...", "status": "pending|done|failed",
      "ts": "...", "done_ts": "", "note": "" }
  ],
  "counters": { "ticks": 0, "bugs_found": 0, "bugs_fixed": 0 },
  "budget": {
    "day": "2026-05-30", "week": "2026-W22",
    "tokens_at_day_start": 0, "tokens_at_week_start": 0,
    "last_total": 0, "last_daily_used": 0, "last_weekly_used": 0
  },
  "cron_job_id": "064d51bd",
  "log": [ { "tick": 1, "ts": "...", "note": "...", "bug": false, "fixed": false, "commit": "" } ]
}
```
Knowledge entries live separately as markdown in `.claude/autopilot/knowledge/*.md`.
Both are runtime — gitignore `.claude/autopilot/`.
```
