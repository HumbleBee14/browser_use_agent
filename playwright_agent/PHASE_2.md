# Phase 2: DOM Extractor + Vision Module

**Status:** Complete
**Files created:** 2 code + 1 test
**Tests:** 10 (Phase 2) + 22 (Phase 1 regression) = 32 total, all passing

---

## What Was Built

### `core/dom_extractor.py` — A11y Tree → LLM-Ready Text

Uses Playwright's `aria_snapshot()` API (native YAML-like accessibility tree) to convert a live page into a compact, indexed text representation for the LLM.

**Pipeline:**
1. `aria_snapshot()` returns structured a11y tree (~2000 nodes on GitHub)
2. Parse into `DOMNode` objects with role, name, URL, state attributes
3. Skip navigation/banner/footer blocks (SKIP_ROLES)
4. Keep only semantic roles (SEMANTIC_ROLES: buttons, links, headings, inputs, etc.)
5. Score by task keywords — boosted nodes first
6. Trim to 40 nodes max

**Output format:**
```
URL: https://github.com/torvalds
Title: torvalds (Linus Torvalds) · GitHub

[0]  [link]  "293k followers" → https://github.com/torvalds?tab=followers
[1]  [heading]  "Linus Torvalds torvalds" (h1)
[2]  [link]  "linux" → /torvalds/linux
[3]  [button]  "Block or Report"
```

**Element map:** Builds `{"0": 'get_by_role("link", name="293k followers")', ...}` — used by `tools/browser.py` for index-based clicking.

**DOM confidence:** Scores page trust (0.0–1.0). Below 0.6 → vision activates.
- Penalizes: canvas elements, SVG icons, missing ARIA labels
- GitHub profile: 0.72 (DOM is sufficient)
- GitHub commit: 0.49 (SVG-heavy → vision needed)

### `core/vision.py` — Screenshot → Claude Multimodal

- `capture_screenshot()` — light theme, animations disabled, PNG output
- `analyze_screenshot()` — sends screenshot + targeted question to Claude vision
- `build_vision_question()` — generates specific questions from DOM gaps
- Rule: **never** "describe this page" — always targeted: "What is the status icon next to X?"

---

## Test Results

### Real Page: GitHub Profile (torvalds)
```
Nodes: 22
Confidence: 0.72
Metrics: semantic=161, interactive=49, svg=94
Element map: 14 entries
Found: followers (293k), pinned repos (linux, GuitarPedal), Follow button, heading
```

### Real Page: GitHub Commit
```
Nodes: 26
Confidence: 0.49 (SVG-heavy → vision would activate)
Found: commit hash, author (benibenj), PR #305569, parent commits, file tree
```

### Screenshot Capture
```
Format: PNG (magic bytes verified)
Size: >1KB (content verified)
Light theme: forced via emulate_media
```

---

## How to Test

```bash
cd playwright_agent

# Phase 2 tests (10 tests — includes 2 browser integration tests)
python tests/test_phase2.py

# Phase 1 regression (22 tests)
python tests/test_phase1.py

# All tests
python tests/test_phase1.py && python tests/test_phase2.py
```

---

## Design Decisions

### Why `aria_snapshot()` over CDP or raw HTML
- Native Playwright API, returns clean YAML-like structure
- Automatically includes ARIA roles, labels, states
- Much smaller than raw HTML (~500 lines vs ~5000)
- CDP `Accessibility.getFullAXTree` available as fallback if needed

### Why skip navigation/banner blocks
- GitHub's nav has 30+ links (Platform, Solutions, Resources, etc.)
- These are noise for task-focused extraction
- `SKIP_ROLES = {"banner", "navigation", "contentinfo"}` removes them at parse time

### Why 40-node budget
- ~20-40 nodes ≈ 500-1000 tokens in the LLM prompt
- Leaves room for system prompt + history + output schema
- Keywords ensure the most relevant nodes survive pruning

---

## What's Next (Phase 3)

- `agent_loop.py` — the core ReAct cycle using DOM extractor + vision + tools
- Wire up: observe (dom_extractor) → decide (Claude tool_use) → act (browser tools) → check
- Test end-to-end on a single GitHub profile extraction
