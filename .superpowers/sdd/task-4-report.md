# Task 4 Report: Import API

## Status

Complete in commit `0d36f77` (`feat(api): import .mirofish.zip and hydrate
Neo4j graph`).

`POST /api/report/import` now validates a multipart transfer bundle, remaps its
IDs, installs project/simulation/report files, retains the graph snapshot, and
hydrates the configured memory backend.

## Implementation

- Saves the upload in a temporary directory and uses the hardened
  `unpack_and_validate`.
- Mints and applies new project, simulation, report, and graph IDs.
- Marks imported project, simulation, and report records complete so existing
  history loaders can read them.
- Writes the remapped graph snapshot to `uploads/exports/<graph_id>.json`.
- Recomputes `live_world` from the installed simulation artifacts.
- Best-effort removes all newly installed paths when graph hydration fails.

## TDD Evidence

### RED

```text
cd backend && python -m pytest tests/test_report_transfer_api.py -k import -v
```

Result: `3 failed`. All import requests returned HTTP 405 because the POST
route did not exist; expected statuses were 400, 200, and 500.

### GREEN

```text
cd backend && python -m pytest tests/test_report_transfer_api.py -v
```

Result: `6 passed`.

Regression command:

```text
cd backend && python -m pytest tests/test_report_transfer.py \
  tests/test_report_transfer_api.py tests/test_graph_hydrate.py -v
```

Result: `15 passed, 1 skipped`. `git diff --check` passed and edited files had
no IDE linter diagnostics.

## Concerns

- Pytest emits the repository's existing `pytest-asyncio` loop-scope
  deprecation warning.

## Review Fix Evidence

- Project, simulation, report, and graph export files are copied into a hidden
  staging directory and only moved to their final upload paths after graph
  hydration succeeds.
- Any install failure removes every final path already moved into place.
- Any failure after hydration starts also calls `delete_graph(graph_id)`
  best-effort, preventing a partially hydrated Neo4j graph from being orphaned.
- The hydrate-failure test now verifies no upload artifacts remain and
  `delete_graph` receives the remapped graph ID. A copy-failure test verifies
  partially staged files are never exposed.

```text
cd backend && python -m pytest tests/test_report_transfer_api.py -v
```

Result: `7 passed`. `git diff --check` passed and edited files had no IDE
linter diagnostics.
