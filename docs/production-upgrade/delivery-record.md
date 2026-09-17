# Checkpoint Delivery Record — Research_Forge

Verified at checkpoint time (2026-09-17). These are the claims confirmed for this
repository during the checkpoint PR; each is grounded in a check run this session,
not in memory.

## Verified claims

- **Wave 2 merge chain:** `origin/main` HEAD is `0e140c2ff59f872286bb25d63188a00387d9900d`
  (`feat(w2): report history, multi-format export, editable outline, dead-code removal`,
  PR #4, MERGED 2026-09-17T18:50:36Z). Chain verified locally:
  `cf07dce` (auth, cost ceiling, sanitized errors — PR #2) → `70a8497` (CRA/craco → Vite
  migration + frontend CI, PR #3) → `0e140c2` (PR #4).
- **Backend tests:** 81 green — verified by the CI `test` job (`pytest backend/tests -v`)
  passing on the merged Wave 2 PRs, and re-run locally during this docs-only checkpoint.
- **Auth coverage:** API-key auth enforced on all 13 API routes (fail-closed, server-issued
  sessions) — delivered in `cf07dce`.
- **Independent audit:** `art_tsz9H96x` (Independent Audit — Research_Forge & SpectraVoice)
  confirmed the delivered merged trees with file/line evidence; included here as
  [independent-audit.md](independent-audit.md).

## Scope of this checkpoint

Docs-only diff: everything under `docs/production-upgrade/` (this folder). No runtime
code, configuration, or dependency changes.
