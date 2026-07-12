---
type: skill
applies_to: chunkhound-development
learned_at: 2026-07-03
tags: [testing, smoke]
confidence: high
---

## Smoke tests before commit

**When to use:** Before committing code changes in ChunkHound.

**Steps:**
1. Run `uv run pytest tests/test_smoke.py -v -n auto`
2. Fix any failures before committing
3. Prefer contract tests over implementation-detail tests

**Note:** Agents must only create new skills after explicit user approval.