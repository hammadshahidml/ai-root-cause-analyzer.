# AI Root Cause Analyzer

An AI-powered incident diagnosis pipeline that watches a live microservice stack, automatically detects failures, and uses an LLM to determine the probable root cause — with calibrated confidence, not blind guessing.

Built as a hands-on systems project to understand log analysis, failure diagnosis, and the practical limits of using LLMs for operational reasoning.

## What it actually does

1. A demo microservice stack (FastAPI + PostgreSQL) runs in Docker.
2. A log collector watches all containers in real time and automatically captures "incident bundles" — the error plus surrounding log context — whenever something fails.
3. An LLM-based diagnosis engine reads each incident and returns a structured root cause, a confidence level, supporting evidence, and a suggested fix.
4. A lightweight retrieval layer surfaces similar past incidents as reference context, without letting the model blindly copy old (possibly wrong) conclusions.
5. An evaluation harness checks the diagnosis engine's output against known ground truth across multiple distinct failure types.

## Architecture

```
demo app (order-service + postgres, Docker)
        │
        ▼
failure injection scripts (wrong password, DB shutdown, slow queries, memory leak)
        │
        ▼
log collector — watches container logs, detects ERROR/FATAL, buffers a
30-second window, saves an "incident bundle" as JSON
        │
        ▼
diagnosis engine — sends the incident + similar past incidents to an LLM,
enforces a confidence-calibration policy, returns structured JSON
        │
        ▼
eval harness — runs known incidents against ground truth, reports
pass/fail on root cause accuracy and confidence calibration
```

## Why this exists

Most "AI diagnoses your errors" demos are a single prompt wrapped around an LLM call. The interesting engineering problem isn't getting an LLM to describe an error — it's getting it to **know when it doesn't have enough evidence to be confident**, and to say so instead of guessing. That's the actual focus of this project.

## Key design decision: confidence calibration

The diagnosis engine classifies each error as either:
- **Self-explanatory** — the error message alone identifies the cause with no other plausible explanation (e.g. `"authentication failed: wrong password"`).
- **Ambiguous** — the error could have multiple distinct causes and requires supporting log evidence before a confident answer is justified (e.g. any network/DNS/connection error).

Network-style errors are treated as ambiguous by default, even when they sound specific, because a message like `"could not translate host name"` can mean a real DNS misconfiguration, a dependency container going down, a typo, or a firewall rule — and a naive system will confidently pick the wrong one.

## What was actually found and fixed along the way

This project surfaced several real bugs, not just "the AI worked":

- **Overconfidence bug**: the first version of the diagnosis engine gave `high confidence` to a DNS error with almost no supporting context, and suggested "verify DNS configuration" — a plausible-sounding but likely wrong fix. Rewriting the confidence-classification rule and re-testing against the same incident dropped it to the correct `low` confidence with an honestly hedged answer.
- **RAG confirmation-bias risk**: adding retrieval of similar past incidents created a risk that the model would copy a prior (possibly wrong) diagnosis instead of evaluating fresh evidence. The prompt explicitly instructs the model to treat past incidents as reference only and re-evaluate independently. Verified by confirming a low-confidence incident did *not* get pulled toward high confidence just because a similar high-confidence incident existed in the retrieval set.
- **Race condition in the log collector**: under a rapid failure burst (e.g. a crash-restart loop), the collector could write two incident files to the same path concurrently, corrupting the JSON. Fixed with atomic writes (write to a temp file, then rename).
- **Unbounded incident growth during crash loops**: overlapping active incident windows during a tight restart loop caused individual incident files to balloon in size (some over 75KB), which exceeded the LLM provider's token-per-minute limit. Fixed by capping the number of log lines sent per diagnosis request.
- **Known, documented limitation — latency detection**: the collector currently only triggers on `ERROR`/`FATAL` log lines. A slow database query that eventually succeeds produces no error log, so it is not detected as an incident. A production version would need duration-based thresholds, not just error-level scanning. This was verified by directly testing the `/debug/slow-query` endpoint and confirming no incident was captured.

## Evaluation results

An automated eval harness (`ai-engine/eval_harness.py`) checks the diagnosis engine against three distinct, verified failure types with known ground truth:

| Incident type | Expected confidence | Result |
|---|---|---|
| DNS/network error, thin log context | Low | ✅ Pass |
| Postgres shutdown, rich log context | High | ✅ Pass |
| Wrong password (self-explanatory) | High | ✅ Pass |

**3/3 root cause matches, 3/3 confidence matches.**

This is a small evaluation set (3 cases) — enough to demonstrate the harness works and that the confidence policy holds across genuinely different failure families, not enough to claim broad statistical confidence. A production version would need 15-20+ cases covering more failure types.

## Project structure

```
.
├── order-service/          # Demo FastAPI + Postgres microservice
├── log-collector/          # Watches Docker logs, captures incident bundles
│   └── incidents/          # Captured incidents + diagnosis records
├── ai-engine/
│   ├── diagnose.py         # LLM-based diagnosis with confidence calibration
│   ├── retrieval.py        # Simple text-similarity retrieval over past incidents
│   └── eval_harness.py     # Automated accuracy/calibration checks
├── failure-injection/      # Scripts to simulate real failures
│   ├── wrong_password.ps1
│   ├── revert_password.ps1
│   ├── memory_leak.ps1
│   └── latency.ps1
└── docker-compose.yml
```

## Running it locally

**Requirements:** Docker Desktop, Python 3.11+, a Groq API key (free tier).

```bash
# 1. Start the demo stack
docker compose up --build

# 2. In a separate terminal, start the log collector
python log-collector/collector.py

# 3. Set your API key
export GROQ_API_KEY="your-key-here"    # PowerShell: $env:GROQ_API_KEY = "..."

# 4. Trigger a failure (example: wrong password)
cd failure-injection
./wrong_password.ps1
curl http://localhost:8000/health      # triggers and confirms the failure
./revert_password.ps1                  # restore normal operation

# 5. Diagnose the captured incident
python ai-engine/diagnose.py "log-collector/incidents/<incident-file>.json"

# 6. Run the full evaluation suite
python ai-engine/eval_harness.py
```

## What I'd build next

- Duration-based incident detection, to catch latency/performance issues alongside hard errors.
- Deduplication of near-identical incidents during a crash loop, instead of capturing one per restart.
- A larger, more varied eval set (15-20+ cases) covering memory exhaustion and multi-service cascading failures.
- Embedding-based retrieval instead of plain text similarity, once the incident dataset is large enough to justify it.

## Tech stack

FastAPI · SQLAlchemy · PostgreSQL · Docker Compose · Python `docker` SDK · Groq API (Llama 3.3 70B) · plain-Python retrieval and evaluation (no external frameworks)
