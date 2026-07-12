# AI Root Cause Analyzer

**[🔴 Live Dashboard](https://hammadshahidml.github.io/ai-root-cause-analyzer/dashboard/)** — view diagnosed incidents in a real-time-styled console (static snapshot, read-only).

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
log collector — watches container logs, detects ERROR/FATAL and SLOW_REQUEST
markers, deduplicates repeated occurrences of the same error, buffers a
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
- **Self-explanatory** — the error message alone identifies the cause with no other plausible explanation (e.g. `"authentication failed: wrong password"`, or a `SLOW_REQUEST` marker stating exactly how long a request took against a known threshold).
- **Ambiguous** — the error could have multiple distinct causes and requires supporting log evidence before a confident answer is justified (e.g. any network/DNS/connection error).

Network-style errors are treated as ambiguous by default, even when they sound specific, because a message like `"could not translate host name"` can mean a real DNS misconfiguration, a dependency container going down, a typo, or a firewall rule — and a naive system will confidently pick the wrong one.

## What was actually found and fixed along the way

This project surfaced several real bugs, not just "the AI worked":

- **Overconfidence bug**: the first version of the diagnosis engine gave `high confidence` to a DNS error with almost no supporting context, and suggested "verify DNS configuration" — a plausible-sounding but likely wrong fix. Rewriting the confidence-classification rule and re-testing against the same incident dropped it to the correct `low` confidence with an honestly hedged answer.
- **RAG confirmation-bias risk**: adding retrieval of similar past incidents created a risk that the model would copy a prior (possibly wrong) diagnosis instead of evaluating fresh evidence. The prompt explicitly instructs the model to treat past incidents as reference only and re-evaluate independently. Verified by confirming a low-confidence incident did *not* get pulled toward high confidence just because a similar high-confidence incident existed in the retrieval set.
- **Race condition in the log collector**: under a rapid failure burst (e.g. a crash-restart loop), the collector could write two incident files to the same path concurrently, corrupting the JSON. Fixed with atomic writes (write to a temp file, then rename).
- **Token-limit overflow, found twice**: overlapping active incident windows during a tight restart loop caused individual incident files to balloon in size (some over 75KB), exceeding the LLM provider's token-per-minute limit. First fixed by capping the log lines sent for the *current* incident — but the same failure recurred later because RAG-retrieved *past* incidents weren't being trimmed either, and a large historical record alone pushed a small new incident's request over the limit. Both the current and retrieved incidents are now capped independently.
- **Detection blind spot — latency (found, then fixed)**: the collector originally only triggered on `ERROR`/`FATAL` log lines. A slow database query that eventually succeeded produced no error log, so it went undetected. Fixed by adding request-duration tracking to the app's middleware and a `SLOW_REQUEST` marker the collector watches for as a second trigger type. Verified: the exact test that previously captured nothing now correctly captures and correctly diagnoses the incident as a latency issue with appropriate confidence.
- **Duplicate incidents during crash loops (found, then fixed)**: a tight restart loop (e.g. from a wrong password) could generate 15+ near-identical incident files for what was really one ongoing failure. Fixed with a deduplication mechanism: the collector normalizes each error message (stripping timestamps/PIDs, which change on every occurrence) into a signature, and merges repeats of the same signature from the same container within a 20-second window into a single incident with an `occurrence_count`. Verified against a real crash loop: 15 repeated failures correctly collapsed into 1 file.
- **Detection blind spot — OOM kills (found, not yet fixed)**: tested by deliberately triggering a memory leak inside a memory-capped container (`mem_limit: 200m`, so the failure stayed contained to the container rather than risking the host machine). Docker correctly killed and restarted the container (`exited with code 137`), but the collector captured **no incident** for the kill itself. Unlike every other failure tested, an OOM kill terminates the process before it can log anything about its own death — there is no `ERROR` line to scan for. Detecting this would require watching Docker's container *event stream* (`die` events with non-zero exit codes via the `docker` SDK), a genuinely different mechanism from the log-content scanning this collector currently uses. Documented as a known limitation rather than fixed, to avoid a same-day, under-tested architecture change.

## Evaluation results

An automated eval harness (`ai-engine/eval_harness.py`) checks the diagnosis engine against four distinct, verified failure types with known ground truth:

| Incident type | Expected confidence | Result |
|---|---|---|
| DNS/network error, thin log context | Low | ✅ Pass |
| Postgres shutdown, rich log context | High | ✅ Pass |
| Wrong password (self-explanatory) | High | ✅ Pass |
| Slow request / latency (self-explanatory symptom) | High | ✅ Pass |

**4/4 root cause matches, 4/4 confidence matches.**

This is still a small evaluation set. It's enough to demonstrate the harness works and that the confidence policy holds across genuinely different failure families, not enough to claim broad statistical confidence. A production version would need 15-20+ cases.

## Deliberately deferred: embedding-based retrieval

The current retrieval layer (`ai-engine/retrieval.py`) uses plain text similarity (`difflib.SequenceMatcher`) over a handful of incidents, with no external dependencies. Embedding-based retrieval was considered and explicitly not built, for two reasons: the incident dataset is currently too small to meaningfully benefit from it, and a local embedding model adds a heavy dependency footprint (`sentence-transformers` pulls in `torch`) on hardware that was already a constraint throughout this project. This is a scoping decision, not an oversight — worth revisiting once the incident dataset is large and varied enough to justify it.

## Project structure

```
.
├── order-service/          # Demo FastAPI + Postgres microservice
│   └── main.py              # Includes /debug/leak and /debug/slow-query
│                             # test-only endpoints, and duration-based
│                             # SLOW_REQUEST logging in middleware
├── log-collector/          # Watches Docker logs, captures incident bundles
│   ├── collector.py         # Atomic writes, deduplication, dual trigger
│   │                         # types (error logs + slow requests)
│   └── incidents/          # Captured incidents + diagnosis records
├── ai-engine/
│   ├── diagnose.py         # LLM-based diagnosis with confidence calibration
│   │                         # and token-budget-aware truncation
│   ├── retrieval.py         # Simple text-similarity retrieval over past incidents
│   └── eval_harness.py     # Automated accuracy/calibration checks
├── failure-injection/      # Scripts to simulate real failures
│   ├── wrong_password.ps1
│   ├── revert_password.ps1
│   ├── memory_leak.ps1
│   └── latency.ps1
└── docker-compose.yml       # order-service is memory-capped (mem_limit: 200m)
                              # so OOM testing stays contained to the container
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

- Container-event-based detection (via the Docker SDK's event stream) to catch OOM kills and other failures that terminate a process before it can log anything.
- A larger, more varied eval set (15-20+ cases) covering multi-service cascading failures.
- Embedding-based retrieval, once the incident dataset is large enough to justify the added dependency weight.
- Investigate why the memory-leak test exceeded the configured 200MB `mem_limit` before triggering an OOM kill (observed around 340MB) — likely interpreter/framework overhead beyond the raw leaked bytes, worth confirming precisely.

## Tech stack

FastAPI · SQLAlchemy · PostgreSQL · Docker Compose · Python `docker` SDK · Groq API (Llama 3.3 70B) · plain-Python retrieval and evaluation (no external frameworks)
