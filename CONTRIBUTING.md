# Contributing

Thanks for helping improve `mdex`.

## Development Setup

```bash
python -m pip install --upgrade pip
python .github/scripts/install_from_pylock.py --lock pylock.toml --editable .
```

Fallback (when you intentionally refresh lock inputs locally):

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

Lockfile refresh:

```bash
python -m pip lock -e ".[dev]" -o pylock.toml
```

## Run Tests

```bash
python -m pytest -q
```

## Compatibility Guardrails

- Primary CI includes Python 3.10, so Python 3.11+ stdlib imports such as `tomllib` must use a `tomli` fallback or a preloaded shim in test-only code.
- Bootstrap scripts that run before lockfile dependencies are installed must not require packages provided by that same lockfile. Prefer stdlib modules first, then tightly scoped vendored fallbacks when unavoidable.

## Contribution Scope

- Keep `README.md` as workflow contract.
- Keep `AGENT.md` as execution heuristics.
- Keep output field names stable unless intentionally versioned.

## Commit Expectations

- Use focused commits (one concern per commit when possible).
- Explain behavior changes in commit messages, not only refactors.
- For contract changes, update:
  - `schemas/*.schema.json`
  - `docs/schema_versioning.md`
  - `CHANGELOG.md`

## Relationship to `docs/convention.md`

`docs/convention.md` defines input note discipline for repositories indexed by `mdex`.
It is not a contribution style guide for this codebase.

## Pull Request Checklist

- [ ] tests pass (`python -m pytest -q`)
- [ ] Python 3.10 compatibility and bootstrap-safe imports still hold for touched files
- [ ] contract changes reflected in `schemas/` and docs
- [ ] README/AGENT links remain valid
- [ ] security-sensitive behavior is covered by tests
- [ ] changelog entry added for user-visible behavior
