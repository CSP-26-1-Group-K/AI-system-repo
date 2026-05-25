# CSP-2026-K AI System Repository

This repository tracks the shareable documents and HomeSense prototype code snapshot for Team K's smart-home robot subscription service validation project.

## Contents

- `docs/`
  - project status
  - presentation drafts
  - network stack Q&A notes
  - sensor placement notes
  - latest presentation PDF
- `homesense-demo/`
  - HomeSense live demo code copied from the local BEHAVIOR-1K working tree
  - FastAPI live server
  - smart-home sensor logic
  - Electron client
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

The current prototype runs OmniGibson / Isaac Sim on a server machine and exposes a FastAPI live gateway. The Electron client connects to the server URL, displays the MJPEG camera stream, receives state updates over WebSocket, and sends command requests over HTTP.

Main communication channels:

- HTTP REST command API
- WebSocket state stream
- MJPEG camera stream

See `docs/NETWORK_TECH_STACK_QA.md` for presentation Q&A notes.

