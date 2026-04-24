# Release Process

This workflow is intentionally manual-first. Publishing stays disabled in practice
until PyPI Trusted Publisher registration is complete.

## Trusted Publisher Setup (PyPI)

1. Create a PyPI account and project ownership for `mdex-cli`.
2. In PyPI, add a Trusted Publisher:
   - Project name: `mdex-cli`
   - Repository: `syaripin-i8i/mdex`
   - Workflow: `.github/workflows/release.yml`
   - Environment: optional (recommended if you use protected environments)
3. Keep `.github/workflows/release.yml` on `workflow_dispatch` only until setup is verified.

## Pre-release Checklist

1. Update `pylock.toml`:
   - `python -m pip lock -e ".[dev]" -o pylock.toml`
2. Update release hash catalog:
   - `python .github/scripts/export_release_hashes.py --lock pylock.toml --output .github/locks/pypi_release_hashes.json`
3. Update `CHANGELOG.md`.
4. Run local verification:
   - `python -m pytest -q`
   - `python -m build`
   - `python -m twine check dist/*`

## Manual Release Run

Run from CLI:

```bash
gh workflow run release.yml
```

The workflow performs:

1. lockfile-driven install (`python .github/scripts/install_from_pylock.py --lock pylock.toml --editable .`)
2. build (`python -m build`)
3. metadata validation (`python -m twine check dist/*`)
4. sdist install smoke (`pip install dist/*.tar.gz` then `mdex --help`)
5. wheel install smoke (`pip install dist/*.whl` then `mdex --help`)
6. Trusted Publishing upload with attestation

## Future Automation

Tag-triggered automatic release is intentionally not enabled yet.
If enabled later, keep manual dispatch as a safe fallback.
