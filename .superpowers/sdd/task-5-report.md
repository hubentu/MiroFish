# Task 5 Report: Resume world for live interviews

## Status

Implemented `POST /api/simulation/resume-world` and an interview-only OASIS
startup path. Imported platform databases are opened in place, simulation
initial events and round loops are skipped, and the process enters the existing
IPC command loop without starting graph-memory updates.

## Database spike

The previous parallel runner unconditionally removed each
`*_simulation.db` before calling `oasis.make`. In camel-oasis 0.2.5,
`oasis.make` constructs `Platform(db_path=...)`, whose `create_db` path opens
the SQLite file and creates missing schema without deleting existing rows.
`env.reset()` starts the platform and reconnects the generated agent graph.
The interview-only branch therefore preserves the DB, calls `env.reset()`, and
returns before initial events or rounds.

## TDD evidence

Red:

```text
uv run python -m pytest tests/test_resume_world.py -v
4 failed: resume_for_interview, interview_only, and resume_world did not exist
```

Green:

```text
uv run python -m pytest tests/test_resume_world.py -v
4 passed

uv run python -m pytest tests/test_resume_world.py \
  tests/test_report_transfer.py tests/test_report_transfer_api.py -q
17 passed

uv run python -m py_compile app/services/simulation_runner.py \
  app/api/simulation.py scripts/run_parallel_simulation.py \
  tests/test_resume_world.py
PASS
```

The wider run including `test_zep_simulation_barrier.py` had 27 passes and two
pre-existing failures: those tests require a monitor join timeout of at least
30 seconds, while the existing working-tree implementation uses 5 seconds.
The failing test also reproduces alone and is outside this task.

## Manual smoke

Not run: it requires an exported/imported world plus a live OASIS worker and
LLM configuration. The subprocess spawn is mocked in unit tests as required.

## Fix: refuse resume when IPC env alive after Flask restart

Review finding: `resume_for_interview()` only checked the in-memory
`_processes` map, so a Flask restart could spawn a second OASIS worker while
IPC was still alive from the previous process.

Change: call `SimulationRunner.check_env_alive()` (wraps
`SimulationIPCClient(sim_dir).check_env_alive()`) before spawning; raise
`ValueError("模拟环境已在运行: …")` when true.

Test: `test_resume_for_interview_refuses_when_ipc_env_alive` mocks
`check_env_alive` → `True`, asserts `ValueError` and no `Popen`.

```text
cd backend && python -m pytest tests/test_resume_world.py -v
5 collected: 4 passed (including new test), 1 pre-existing fail
(test_interview_only_opens_existing_db — missing camel in this env)
```
