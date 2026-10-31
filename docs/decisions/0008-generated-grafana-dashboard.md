# 0008. Generate the Grafana dashboard from code

- **Status:** accepted
- **Date:** 2026-10-31

## Context
A Grafana dashboard is ~3000 lines of JSON. Edited by hand (or exported from the UI) it drifts: copied
panels keep a wrong query, colours and thresholds differ between panels, reviews of the diff are useless.

## Decision
`deploy/observability/grafana/build_dashboard.py` builds the JSON: one function call per panel, shared
thresholds and colours. CI runs it with `--check` and fails if the committed JSON doesn't match.

## Consequences
- Changes are reviewed as a few lines of Python; the dashboard stays consistent.
- Edits made in the Grafana UI can't be saved to a provisioned dashboard; changing it means editing the
  script (documented in deploy/README.md).
