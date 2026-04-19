# Contributing

Thanks for helping improve `mdex`.

## Development Setup

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

## Run Tests

```bash
python -m pytest -q
```

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
- [ ] contract changes reflected in `schemas/` and docs
- [ ] README/AGENT links remain valid
- [ ] security-sensitive behavior is covered by tests
- [ ] changelog entry added for user-visible behavior
