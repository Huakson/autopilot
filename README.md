# autopilot

**A Claude Code skill that runs autonomously for long stretches** — testing your
app, finding and fixing bugs on its own, committing to an isolated branch, with a
**budget guard** that **stops at 90% of your daily token cap** and **limits to 80%
of the weekly cap**.

Inspired by [@brunobertolini](https://gist.github.com/brunobertolini/d583141b9909909eeaba6273ff87cdc0)'s
pattern (on-disk state file + self-contained prompt fired by cron + dogfooding
skills), with **budget guards** added on top.

---

## TL;DR

```
/autopilot setup     # configure caps + targets, create branch, arm the cron, run the first tick
/autopilot status    # status, ticks, bugs, % of daily/weekly budget
/autopilot stop      # kill the cron and stop
```

The cron fires `/autopilot tick` hourly. Each tick: run one target → find a bug →
fix it → commit/push to the autopilot branch → **stop when the budget is
exhausted**. You merge later.

---

## How it works (architecture)

```
┌──────────────────────────────────────────────────────────────┐
│  cron (CronCreate, recurring)  ──fires──▶  "/autopilot tick"  │
└──────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
   ┌───────────────────────────────────────────────────────────┐
   │  SKILL.md (the "brain" — the model follows the steps)      │
   │   1. gate  → autopilot.py measures tokens + decides         │
   │   2. if stop → CronDelete + report + END                   │
   │   3. if continue → run 1 target → fix → commit/push        │
   └───────────────────────────────────────────────────────────┘
                                   │
                ┌──────────────────┴───────────────────┐
                ▼                                       ▼
   ┌────────────────────────┐            ┌──────────────────────────┐
   │ scripts/autopilot.py   │            │ .claude/autopilot/        │
   │ (deterministic engine) │◀──read/────│ state.json (source of    │
   │  - measures tokens     │    write   │ truth; survives session  │
   │  - budget + rollover   │            │ death/resume)            │
   │  - gate verdict        │            └──────────────────────────┘
   └───────────┬────────────┘
               │ reads
               ▼
   ~/.claude/projects/<cwd-encoded>/*.jsonl   (session transcripts)
```

**3 pieces:**
- **`SKILL.md`** — what Claude does in each mode (setup/tick/stop/status). It's
  text; the model follows it.
- **`scripts/autopilot.py`** — the deterministic engine. **All budget arithmetic
  and token counting live here** (the model never counts tokens by hand).
- **`.claude/autopilot/state.json`** — on-disk source of truth. Persists config,
  counters, budget checkpoints, and a log. Survives session death (resume).

**Why it survives a dying session:** the cron dies with the session, but
`state.json` persists. Reopened? `/autopilot tick` resumes from saved state and
re-arms the cron if needed.

---

## Budget guards (the differentiator)

- **Unit:** tokens measured from the session `.jsonl` transcripts
  (`input + output + cache_creation` per message, summed across all files).
  `cache_read` is **ignored by default** (it's cheap and inflated by the context
  re-read every turn; add it with `--include-cache-read`).
- **Daily:** when it hits **90%** of `daily_budget_tokens` → **STOP**.
- **Weekly:** when it hits **80%** of `weekly_budget_tokens` → **STOP**.
- **Automatic rollover:** day/week turns over → checkpoint resets on its own.
- Deterministic: `autopilot.py gate` computes and returns `{"verdict":"continue|stop"}`.

> Tune the caps to your plan. You can't read the real account rate-limit % from
> inside Claude Code — so the budget is measured **locally** against a number you
> set.

---

## Safety (hard rules)

- **Never** merges to the default branch. **Never** force-pushes. Only
  commits/pushes to the `autopilot/<timestamp>` branch — **you do the merge**.
- **1 fix per tick** (bounded cost).
- **Kill switch:** `status=stopped` halts everything. `/autopilot stop` kills the cron.
- Respects the repo's conventions (commit style, CLAUDE.md/AGENTS.md).
- Never touches secrets / `.env` / production. Local stack/DB only.
- Stops by itself on anything weird (abnormal burn, repeated error with no progress).

---

## Install

The skill must live in `~/.claude/skills/autopilot/` (user-level, available in any
project) or in `<repo>/.claude/skills/autopilot/` (per project).

```bash
git clone https://github.com/Huakson/autopilot.git
cd autopilot
./install.sh            # copies into ~/.claude/skills/autopilot/
```

Or manually:
```bash
mkdir -p ~/.claude/skills/autopilot/scripts
cp SKILL.md ~/.claude/skills/autopilot/SKILL.md
cp scripts/autopilot.py ~/.claude/skills/autopilot/scripts/autopilot.py
chmod +x ~/.claude/skills/autopilot/scripts/autopilot.py
```

Requirements: **Claude Code** (with the `CronCreate` tool), **Python 3** (stdlib
only), `git`. No external dependencies.

---

## Usage

### 1. Setup
In Claude Code, inside the project you want to test:
```
/autopilot setup
```
It asks (or uses defaults):
- daily token cap (default 30,000,000)
- weekly token cap (default 150,000,000)
- cron (default `0 * * * *` = hourly)
- **targets**: the commands to run each tick (e.g. `go test ./...`, `npm test`,
  `pytest -q`, a Playwright spec, a key user flow)

Then it: confirms a clean tree → creates branch `autopilot/<date-time>` → writes
the state → arms the cron → runs the first tick.

### 2. Let it run
The cron fires `/autopilot tick` at the configured interval. Each tick:
1. `gate` measures tokens and decides.
2. `stop` → kills the cron, reports, ends.
3. `continue` → runs 1 target (rotating); if it fails, fixes → re-runs until green
   → commits/pushes to the branch.

### 3. Watch / stop
```
/autopilot status    # summary
/autopilot stop      # stop everything
```

### 4. Resume (session died)
Reopened Claude Code? `/autopilot tick` resumes from `state.json` (the gate decides
whether budget remains) and re-arms the cron if needed.

---

## Engine commands (`autopilot.py`)

You normally don't call these directly (the skill does), but for debugging:

```bash
ENGINE=~/.claude/skills/autopilot/scripts/autopilot.py

python3 $ENGINE tokens                       # token total measured now
python3 $ENGINE init --daily 30000000 --weekly 150000000 \
        --cron "0 * * * *" --branch autopilot/2026-05-30-1200 \
        --target "go test ./..." --target "npm test"
python3 $ENGINE gate                         # {verdict, reason, daily_pct_used, ...}
python3 $ENGINE log --note "fix: X" --bug --fixed --commit abc123
python3 $ENGINE status
python3 $ENGINE stop --reason "manual"
python3 $ENGINE set-cron --cron-job-id <id>
```

Default state path: `./.claude/autopilot/state.json` (override with `--state`).

### Token metric
`input + output + cache_creation` summed across
`~/.claude/projects/<cwd-encoded>/*.jsonl`. The cwd is encoded by replacing `/` and
`.` with `-` (e.g. `/Users/x/proj/.claude/wt` → `-Users-x-proj--claude-wt`).
`cache_read` is added only with `--include-cache-read`.

---

## State (`state.json`)

```json
{
  "status": "running",
  "stop_reason": "",
  "branch": "autopilot/2026-05-30-1200",
  "created_at": "...",
  "config": {
    "daily_budget_tokens": 30000000,
    "weekly_budget_tokens": 150000000,
    "daily_stop_pct": 0.9,
    "weekly_stop_pct": 0.8,
    "schedule_cron": "0 * * * *",
    "max_fixes_per_tick": 1,
    "include_cache_read": false,
    "targets": ["go test ./...", "npm test"]
  },
  "counters": { "ticks": 0, "bugs_found": 0, "bugs_fixed": 0 },
  "budget": {
    "day": "2026-05-30", "week": "2026-W22",
    "tokens_at_day_start": 0, "tokens_at_week_start": 0,
    "last_total": 0, "last_daily_used": 0, "last_weekly_used": 0
  },
  "cron_job_id": "",
  "log": []
}
```

> Runtime artifact — **do not commit**. Add `.claude/autopilot/` to your project's
> `.gitignore`.

---

## Adapting to your project

The skill is generic: the test commands are the **targets** you pass at setup.
Examples:
- Go: `--target "go test ./..."`
- Node: `--target "npm test"` or `--target "npx playwright test e2e/foo.spec.ts"`
- Python: `--target "pytest -q"`
- Key flow: describe it as a step the model executes (it follows SKILL.md).

Want to change caps/cron/safety? Edit `config` at setup, or `SKILL.md`.

---

## Caveats

- **Burns context fast.** On capped plans the guards (90%/80%) cut before the
  limit, but check `/autopilot status` now and then.
- The cron dies if the session dies — use RESUME.
- 1 fix per tick is intentional (predictable cost). For more throughput, tune the
  cron interval (not the fix count).
- Runs against your **local** stack/DB. Don't point it at production.

---

## Credits

Original pattern (state file + cron + dogfooding) by
[Bruno Bertolini](https://gist.github.com/brunobertolini/d583141b9909909eeaba6273ff87cdc0).
This version adds the budget guards (90% daily / 80% weekly), a deterministic
token-counting engine, and safety rules (isolated branch, no merge).

## License

MIT — see [LICENSE](LICENSE).
