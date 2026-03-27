# Phase 4: Orchestrator + Workers + Discovery

**Status:** Complete
**Files created:** 4 (`main.py`, `worker.py`, `discover.py`, `tasks/inputs/github_profiles.csv`)

---

## What Was Built

### `worker.py` — Isolated BrowserContext Per Sample
- Each sample gets its own BrowserContext (own cookies, session — no bleed)
- If `auth_profile` set, loads storage_state for logged-in sessions
- All exceptions caught → written to result.json, never crashes batch
- Context always closed in `finally`

### `main.py` — Orchestrator
- Two input modes: `--input samples.csv` (batch) or `--url` + `--id` (single)
- `asyncio.gather` with `Semaphore(N)` for bounded concurrency
- Idempotent restart: skips samples where result.json already has `status: done`
- Merges all result.json → combined.csv at batch end (sorted by sample_id)
- Rich console output with live progress + summary table
- CLI flags: `--task`, `--input`, `--url`, `--id`, `--concurrency`, `--headless`

### `discover.py` — Phase 1 Discovery
- Runs agent_loop with a discovery task spec (same loop, different goal)
- Agent paginates start URL, collects sample URLs
- Writes samples.csv with sample_id, url, discovered_at
- Standalone CLI: `python discover.py --task ... --start-url ... --output samples.csv`

---

## Test Results

### Batch: 3 GitHub Profiles (concurrency=3)
```
python main.py --task tasks/github_profile.json --input tasks/inputs/github_profiles.csv --concurrency 3

  [1/3] torvalds   DONE    (34.3s)
  [2/3] gvanrossum DONE    (38.2s)
  [3/3] DHH        FAILED  (49.2s)  — exhausted 12 steps

  Total: 51.7s for 3 samples
```

### combined.csv
```csv
sample_id,status,display_name,bio,company,location,followers,following,pinned_repos
DHH,failed,,,,,,,
gvanrossum,done,Guido van Rossum,,,,25.9k,5,"[""microsoft/typeagent-py""]"
torvalds,done,Linus Torvalds,,,,293k,0,"[""linux"", ""GuitarPedal""]"
```

### Evidence Files Per Sample
```
evidence/run_2026-03-27_HHMMSS/
├── torvalds/
│   ├── 01_profile.png, 02_profile.png, ...
│   ├── result.json
│   └── action_log.json
├── gvanrossum/
│   ├── 01_profile.png, ...
│   ├── result.json
│   └── action_log.json
├── DHH/
│   ├── result.json (status: failed)
│   └── action_log.json
```

---

## How to Run

```bash
cd playwright_agent

# Batch from CSV (3 profiles, 3 parallel browsers)
python main.py --task tasks/github_profile.json --input tasks/inputs/github_profiles.csv --concurrency 3

# Single sample
python main.py --task tasks/github_profile.json --url https://github.com/torvalds --id torvalds

# Headless mode
python main.py --task tasks/github_profile.json --input tasks/inputs/github_profiles.csv --headless

# Discovery (not yet tested end-to-end)
python discover.py --task tasks/github_discovery.json --start-url https://github.com/orgs/microsoft/people
```

---

## What the Full System Looks Like Now

```
User: python main.py --task tasks/github_profile.json --input samples.csv --concurrency 5

main.py (orchestrator)
  → loads task spec + samples CSV
  → skips already-completed samples (idempotent)
  → launches 5 parallel workers via asyncio.gather + Semaphore

worker.py (per sample)
  → creates isolated BrowserContext
  → calls agent_loop.run()
  → catches all exceptions → result.json

agent_loop.py (the brain)
  → OBSERVE: dom_extractor.snapshot() → pruned a11y tree
  → DECIDE: Claude tool_use (9 actions, tool_choice=any)
  → ACT: browser tools (goto, click, screenshot, etc.)
  → CHECK: done validation (required fields + artifacts)
  → repeat until done/fail/max_steps

tools/output.py (evidence)
  → screenshots with SHA-256
  → result.json + action_log.json

main.py (merge)
  → reads all result.json → combined.csv (sorted)
  → prints summary table
```

---

## What's Next

The 4-phase build is complete. The system can:
- Extract profiles from any website (task spec JSON)
- Run in parallel with bounded concurrency
- Produce evidence-grade outputs (SHA-256, provenance, action logs)
- Handle failures gracefully (result.json always written)
- Restart idempotently after crashes

Remaining work:
- More task specs (commit audit, LinkedIn enrichment, form fill)
- Discovery end-to-end testing
- Scale testing (50+ samples)
- README polish
