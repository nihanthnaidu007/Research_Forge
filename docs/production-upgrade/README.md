# Production Upgrade — Delivery Checkpoint

This folder is the **checkpoint paper trail** of the four-repo production upgrade program
(Nixus-Sql, AXIOM_Adaptive_RAG, Research_Forge, SpectraVoice), committed into
`nihanthnaidu007/Research_Forge` as that repository's delivery record.

## Contents

| File | What it is | Source |
|---|---|---|
| [dossier.md](dossier.md) | Final delivery dossier across all four repos — verified merge points, limitations, restoration checkpoints | Project artifact `art_jV2n9Tta` (FINAL, 2026-09-17) |
| [master-plan.md](master-plan.md) | Roadmap, acceptance criteria, CI-gated PR model, scope, operator/Mac constraints | Project artifact `art_gwXo54Vz` |
| [survey.md](survey.md) | Research_Forge production-readiness survey | Project artifact `art_2Hi9nExY` |
| [independent-audit.md](independent-audit.md) | Independent audit of Research_Forge & SpectraVoice delivered state (merged trees confirmed with file/line evidence) | Project artifact `art_tsz9H96x` |
| [w2-pr-record.md](w2-pr-record.md) | Research_Forge Wave 2 PR record (PR #4, merged at `0e140c2`) | Project artifact `art_Uyjequ8Q` + GitHub PR data |
| [delivery-record.md](delivery-record.md) | Checkpoint-time verified claims for this repo (merge SHAs, test counts, auth coverage) | Verified at checkpoint time |

## Delivery state

Research_Forge main is at `0e140c2` — the verified end of the Wave 2 chain
`cf07dce → 70a8497 → 0e140c2` — with all backend tests green, auth on all 13 API
routes, and the tree independently audited. The annotated tag
`production-upgrade/final` is pushed at the merge commit of the checkpoint PR.
