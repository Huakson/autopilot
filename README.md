# autopilot

**Skill do Claude Code pra rodar autônomo por longos períodos** — testando a
app, achando e corrigindo bugs sozinho, commitando numa branch isolada, com
**guarda de budget** que **para em 90% do teto diário de tokens** e **limita a
80% do teto semanal**.

Inspirado no padrão de [@brunobertolini](https://gist.github.com/brunobertolini/d583141b9909909eeaba6273ff87cdc0)
(state file no disco + prompt self-contained disparado por cron + skills de
dogfood), com a adição das **guardas de budget**.

---

## TL;DR

```
/autopilot setup     # configura tetos + targets, cria branch, arma o cron, roda o 1º tick
/autopilot status    # status, ticks, bugs, % do budget diário/semanal
/autopilot stop      # mata o cron e para
```

O cron dispara `/autopilot tick` de hora em hora. Cada tick: roda 1 target →
acha bug → corrige → commita/pusha na branch do autopilot → **para quando o
budget estoura**. Você faz o merge depois.

---

## Como funciona (arquitetura)

```
┌──────────────────────────────────────────────────────────────┐
│  cron (CronCreate, recorrente)  ──fires──▶  "/autopilot tick" │
└──────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
   ┌───────────────────────────────────────────────────────────┐
   │  SKILL.md (o "cérebro" — o modelo segue os passos)         │
   │   1. gate  → autopilot.py mede tokens + decide continue/stop│
   │   2. se stop → CronDelete + report + FIM                   │
   │   3. se continue → roda 1 target → fix → commit/push       │
   └───────────────────────────────────────────────────────────┘
                                   │
                ┌──────────────────┴───────────────────┐
                ▼                                       ▼
   ┌────────────────────────┐            ┌──────────────────────────┐
   │ scripts/autopilot.py   │            │ .claude/autopilot/        │
   │ (motor determinístico) │◀──lê/grava─│ state.json (fonte de     │
   │  - mede tokens         │            │ verdade; sobrevive a      │
   │  - budget + rollover   │            │ morte/resume da sessão)   │
   │  - gate verdict        │            └──────────────────────────┘
   └───────────┬────────────┘
               │ lê
               ▼
   ~/.claude/projects/<cwd-encoded>/*.jsonl   (transcripts da sessão)
```

**3 peças:**
- **`SKILL.md`** — o que o Claude faz em cada modo (setup/tick/stop/status). É
  texto; o modelo segue.
- **`scripts/autopilot.py`** — motor determinístico. **Toda aritmética de budget
  e contagem de token vive aqui** (o modelo não conta token na mão).
- **`.claude/autopilot/state.json`** — fonte de verdade no disco. Persiste config,
  contadores, checkpoints de budget e log. Sobrevive à morte da sessão (resume).

**Por que funciona com sessão morrendo:** o cron morre junto com a sessão, mas o
`state.json` persiste. Reabriu? `/autopilot tick` retoma do estado salvo e re-arma
o cron se preciso.

---

## Guardas de budget (o diferencial)

- **Unidade:** tokens medidos dos transcripts `.jsonl` da sessão
  (`input + output + cache_creation` por mensagem, somados em todos os arquivos).
  `cache_read` é **ignorado por default** (é barato e infla por causa do contexto
  relido a cada turno; some com `--include-cache-read` se quiser).
- **Diário:** ao bater **90%** do `daily_budget_tokens` → **PARA**.
- **Semanal:** ao bater **80%** do `weekly_budget_tokens` → **PARA**.
- **Rollover automático:** vira o dia/semana → checkpoint reseta sozinho.
- Determinístico: `autopilot.py gate` calcula e devolve `{"verdict":"continue|stop"}`.

> Ajuste os tetos à realidade do seu plano. Não dá pra ler a % real do rate-limit
> da conta Anthropic de dentro do Claude Code — por isso o budget é medido
> **localmente** contra um número que você define.

---

## Safety (regras invioláveis)

- **Nunca** faz merge na branch default. **Nunca** force-push. Só commita/pusha na
  branch `autopilot/<timestamp>` — **você faz o merge**.
- **1 fix por tick** (custo bounded).
- **Kill switch:** `status=stopped` para tudo. `/autopilot stop` mata o cron.
- Respeita as convenções do repo (estilo de commit, CLAUDE.md/AGENTS.md).
- Nunca toca em segredos / `.env` / produção. Só stack/DB local.
- Para sozinho se ver algo estranho (burn anômalo, erro repetido sem progresso).

---

## Instalação

A skill precisa ficar em `~/.claude/skills/autopilot/` (user-level, disponível em
qualquer projeto) ou em `<repo>/.claude/skills/autopilot/` (por projeto).

```bash
git clone https://github.com/Huakson/autopilot.git
cd autopilot
./install.sh            # copia pra ~/.claude/skills/autopilot/
```

Ou manual:
```bash
mkdir -p ~/.claude/skills/autopilot/scripts
cp SKILL.md ~/.claude/skills/autopilot/SKILL.md
cp scripts/autopilot.py ~/.claude/skills/autopilot/scripts/autopilot.py
chmod +x ~/.claude/skills/autopilot/scripts/autopilot.py
```

Requisitos: **Claude Code** (com o tool `CronCreate`), **Python 3** (stdlib só),
`git`. Nada de dependência externa.

---

## Uso

### 1. Setup
No Claude Code, dentro do projeto que você quer testar:
```
/autopilot setup
```
Ele pergunta (ou usa defaults):
- teto diário de tokens (default 30.000.000)
- teto semanal (default 150.000.000)
- cron (default `0 * * * *` = de hora em hora)
- **targets**: os comandos que ele roda por tick (ex: `go test ./...`,
  `npm test`, `pytest -q`, um spec Playwright, um fluxo-chave)

Aí ele: confirma tree limpo → cria branch `autopilot/<data-hora>` → grava o state
→ arma o cron → roda o 1º tick.

### 2. Deixa rodando
O cron dispara `/autopilot tick` no intervalo configurado. Cada tick:
1. `gate` mede tokens e decide.
2. `stop` → mata o cron, reporta, fim.
3. `continue` → roda 1 target (rotaciona); se falhar, corrige → re-roda até verde
   → commita/pusha na branch.

### 3. Acompanha / para
```
/autopilot status    # resumo
/autopilot stop      # para tudo
```

### 4. Resume (sessão morreu)
Reabriu o Claude Code? `/autopilot tick` retoma do `state.json` (o gate decide se
ainda há budget) e re-arma o cron se preciso.

---

## Comandos do motor (`autopilot.py`)

Normalmente você não chama direto (a skill chama), mas pra debug:

```bash
ENGINE=~/.claude/skills/autopilot/scripts/autopilot.py

python3 $ENGINE tokens                       # total de tokens medido agora
python3 $ENGINE init --daily 30000000 --weekly 150000000 \
        --cron "0 * * * *" --branch autopilot/2026-05-30-1200 \
        --target "go test ./..." --target "npm test"
python3 $ENGINE gate                         # {verdict, reason, daily_pct_used, ...}
python3 $ENGINE log --note "fix: X" --bug --fixed --commit abc123
python3 $ENGINE status
python3 $ENGINE stop --reason "manual"
python3 $ENGINE set-cron --cron-job-id <id>
```

State default: `./.claude/autopilot/state.json` (override com `--state`).

### Métrica de token
`input + output + cache_creation` somados em `~/.claude/projects/<cwd-encoded>/*.jsonl`.
O cwd é codificado trocando `/` e `.` por `-` (ex:
`/Users/x/proj/.claude/wt` → `-Users-x-proj--claude-wt`). `cache_read` entra só
com `--include-cache-read`.

---

## Estado (`state.json`)

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

> Runtime — **não versionar**. Adicione `.claude/autopilot/` ao `.gitignore` do
> seu projeto.

---

## Adaptando pro seu projeto

A skill é genérica: os comandos de teste são os **targets** que você passa no
setup. Exemplos:
- Go: `--target "go test ./..."`
- Node: `--target "npm test"` ou `--target "npx playwright test e2e/foo.spec.ts"`
- Python: `--target "pytest -q"`
- Fluxo-chave: descreva como um passo que o Claude executa (ele segue o SKILL.md).

Quer mudar tetos/cron/safety? Edite `config` no setup ou o `SKILL.md`.

---

## Caveats

- **Queima contexto rápido.** Em planos com teto, as guardas (90%/80%) cortam
  antes do limite, mas dê `/autopilot status` de vez em quando.
- O cron some se a sessão morrer — use o RESUME.
- 1 fix por tick é proposital (custo previsível). Pra mais throughput, ajuste o
  intervalo do cron (não o nº de fixes).
- Roda contra stack/DB **local**. Não aponte pra produção.

---

## Créditos

Padrão original (state file + cron + dogfood) por
[Bruno Bertolini](https://gist.github.com/brunobertolini/d583141b9909909eeaba6273ff87cdc0).
Esta versão adiciona as guardas de budget (90% diário / 80% semanal), motor
determinístico de contagem de token e regras de safety (branch isolada, sem merge).

## Licença

MIT — veja [LICENSE](LICENSE).
