# CSP-2026-K AI System Repository

This repository tracks the shareable documents and HomeSense prototype code snapshot for Team K's smart-home robot / digital-twin data generation project.

## Contents

- `docs/`
  - project status
  - presentation drafts
  - network stack Q&A notes
  - sensor placement notes
  - latest presentation PDF
- `homesense-demo/`
  - HomeSense live demo code snapshot copied from the local BEHAVIOR-1K working tree
  - OmniGibson viewport-first demo runner
  - optional FastAPI live server
  - smart-home sensor logic
  - legacy Electron client
  - compatibility runner files

## Not Included

The following are intentionally excluded from Git:

- BEHAVIOR-1K upstream repository
- OmniGibson / Isaac Sim datasets
- Merom scene asset bundles
- collaborator export archives
- runtime logs
- `node_modules`
- local crash dumps and generated files

Large assets and scene subsets must be shared separately.

## Current Prototype Summary

The current prototype focuses on an OmniGibson / Isaac Sim viewport demo using the `Merom_0_int` digital twin. It supports resident movement, scene camera switching, sensor range visualization, scene-profile based resident randomization, and JSONL episode logging for future replay / policy training.

The Electron/Web path is preserved as a legacy optional monitoring path, but it is no longer the primary demo surface.

Current source-of-truth document:

- `docs/PROJECT_PROGRESS_2026-05-30.md`

See also `docs/NETWORK_TECH_STACK_QA.md` for presentation Q&A notes.
