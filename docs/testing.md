# Testing Guide

A practical reference for writing, running, and debugging tests across the three test layers in this repo.

## Test Layers at a Glance

| Layer | Location | What it tests | Needs a bench? |
|-------|----------|---------------|----------------|
| **Unit** | `tests/admin/`, `tests/pilot/` | Individual classes, helpers, tasks — all fast, fully mocked | No |
| **Integration** | `tests/integration/` | Real `pilot` CLI against a fully-initialised bench on disk | Yes (`pilot init` first) |
| **E2E (Playwright)** | `tests/e2e/` | Browser-driven admin UI through the full bench lifecycle | Yes (managed by harness) |

---

## Setup

Install all dependencies from the repo root once:

```bash
# Core + test extras (unit and integration)
pip install -e ".[test,admin]"

# Also install E2E extras if you will run Playwright tests
pip install -e ".[e2e]"
playwright install chromium   # one-time browser download
```

---

## Unit Tests

Unit tests live under `tests/pilot/` and `tests/admin/`. They run entirely in-process — no bench, database, or network required.

### Running all unit tests

```bash
pytest tests/ --ignore=tests/integration --ignore=tests/e2e
```

### Running a specific module

```bash
pytest tests/pilot/core/test_app_validator.py
```

### Running tests matching a keyword

```bash
pytest tests/pilot/core/test_app_validator.py -k "syntax"
```

### Running with coverage

```bash
pytest tests/ --ignore=tests/integration --ignore=tests/e2e \
    --cov=pilot --cov-report=term-missing
```

To check coverage on a single module:

```bash
pytest tests/pilot/core/test_app_validator.py -k "syntax" \
    --cov=pilot.core.app.validator.syntax --cov-report=term-missing
```

### Running type checks

```bash
mypy pilot admin/backend
```

---

## Writing a Unit Test

Unit tests follow standard `pytest` conventions. Place the test file alongside the module it covers.

### Conventions

- One `test_*.py` file per module or class being tested.
- Test function names describe the scenario: `test_<what>_<expected_outcome>`.
- Use `pytest.MonkeyPatch` to patch filesystem, subprocess, or external calls.
- Use `tmp_path` for any temporary files.
- Never use `print()` in tests — use `assert` statements.

### Example: testing a class method

```python
from pathlib import Path
import pytest
from pilot.core.app.validator.syntax import SyntaxCheck
from pilot.exceptions import AppValidationError


def _make_app(tmp_path: Path, name: str, files: dict[str, str]):
    """Helper: create a minimal app directory tree."""
    from types import SimpleNamespace
    app_path = tmp_path / name
    for rel, content in files.items():
        target = app_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return SimpleNamespace(
        path=app_path,
        module_name=name,
        config=SimpleNamespace(name=name, validation_ignore=[]),
        bench=None,
    )


def test_syntax_check_passes_on_valid_python(tmp_path: Path) -> None:
    """SyntaxCheck.run() must not raise when all Python files are valid."""
    app = _make_app(tmp_path, "myapp", {"myapp/hooks.py": "app_name = 'myapp'\n"})
    SyntaxCheck().run(app)  # no exception = pass


def test_syntax_check_raises_on_invalid_python(tmp_path: Path) -> None:
    """SyntaxCheck.run() must raise AppValidationError for a syntax error."""
    app = _make_app(tmp_path, "myapp", {"myapp/bad.py": "def f(\n"})
    with pytest.raises(AppValidationError, match="bad.py"):
        SyntaxCheck().run(app)
```

### Mocking subprocess calls

```python
import subprocess

def test_raises_when_subprocess_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode=1, stdout="", stderr="fatal error"
        ),
    )
    # ... assert your error is raised
```

---

## Integration Tests

Integration tests live under `tests/integration/`. They run real `pilot` CLI commands against a bench that you initialise once locally.

### One-time setup

```bash
# Create and initialise a test bench (takes 5–10 min, downloads frappe)
mkdir -p benches/test-bench
cp tests/fixtures/ci_bench.toml benches/test-bench/bench.toml
pilot init -b test-bench
```

### Running integration tests

```bash
BENCH_TEST_ROOT=$PWD/benches/test-bench pytest tests/integration/ -v -m "not production"
```

The `BENCH_TEST_ROOT` env var points to your initialised bench. The `conftest.py` skips the suite automatically if no bench is found there, so unit tests are never blocked.

### Production tests (destructive)

Production tests rewrite the system nginx config and provision real SSL. Run them separately:

```bash
BENCH_TEST_ROOT=$PWD/benches/test-bench \
BENCH_E2E_PRODUCTION=1 \
pytest tests/integration/ -v -m "production"
```

> [!CAUTION]
> Production tests modify system services (nginx). Only run on a disposable machine or CI.

### Writing an integration test

```python
import subprocess
from pathlib import Path
import pytest


def test_get_app_installs_correctly(bench_root: Path, bench_bin: str) -> None:
    """pilot get-app must clone and install the app into the bench."""
    result = subprocess.run(
        [bench_bin, "get-app", "https://github.com/frappe/testapp"],
        cwd=bench_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
```

Use the session-scoped fixtures from `tests/integration/conftest.py`:

| Fixture | Type | Provides |
|---------|------|---------|
| `bench_root` | `Path` | Path to the initialised bench; skips if not found |
| `bench_bin` | `str` | Path to the `pilot` binary; skips if not on `PATH` |
| `site_name` | `str` | `site1.localhost` |
| `testapp_repo` | `Path` | A local git repo of `tests/fixtures/testapp` |

---

## E2E Tests (Playwright)

E2E tests live under `tests/e2e/`. They drive the real admin UI in a browser through the full bench lifecycle: wizard → login → site → install app → uninstall → drop site.

### Running locally

```bash
# MariaDB bench
E2E_MARIADB_PASSWORD=admin pytest tests/e2e/

# PostgreSQL bench
E2E_DB_TYPE=postgres E2E_POSTGRES_PASSWORD=admin pytest tests/e2e/

# Watch the browser
pytest tests/e2e/ --headed --slowmo 500
```

The harness creates and tears down its own bench automatically — no manual `pilot init` needed.

### Replaying a trace after a failure

```bash
playwright show-trace test-results/<module>/trace.zip
```

### Useful environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `PILOT_BIN` | `<repo>/bin/pilot` | CLI entry point |
| `E2E_DB_TYPE` | `mariadb` | `mariadb` or `postgres` |
| `E2E_MARIADB_PASSWORD` | `admin` | Root password for the bench's MariaDB server |
| `E2E_POSTGRES_PASSWORD` | `admin` | Superuser password for the bench's PostgreSQL server |
| `E2E_KEEP_ON_FAILURE` | (unset) | Set to keep the bench around after a failure for inspection |
| `E2E_BUILD_ADMIN` | off | `1` builds the admin UI from source before running |

### Writing an E2E test

E2E tests are organized as **serial modules**. Each test in a module runs in order and skips remaining steps if one fails (via the `incremental` marker).

Add new flows in `tests/e2e/flows/` and call them from a spec in `tests/e2e/specs/`:

```python
# tests/e2e/specs/test_my_flow.py
import pytest

pytestmark = [pytest.mark.incremental]


def test_step_one(page, bench) -> None:
    page.goto(bench.admin_url)
    # ... drive the UI


def test_step_two(page, bench) -> None:
    # ... next step in the same bench session
```

Selectors use labels, roles, and visible text — no `data-testid`. If UI copy changes, update the strings in `flows/`.

---

## CI Reference

| Workflow | Trigger | Command |
|----------|---------|---------|
| `unit-tests.yml` | push / PR to `develop` | `pytest tests/ --ignore=tests/integration --ignore=tests/e2e` |
| `integration.yml` | push / PR to `develop` | `pytest tests/integration/` (with a full bench provisioned) |
| `e2e.yml` | push / PR to `develop` | `pytest tests/e2e/` (matrix: mariadb × postgres) |

All three must pass before merging to `develop`.

