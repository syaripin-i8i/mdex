# Release Process

Releases are intentionally manual-first. PyPI Trusted Publishing is configured and
has been used since the `0.4.0` release. The workflow accepts only a version-matching
tag with matching release metadata.

## Trusted Publisher Configuration

- Project name: `mdex-cli`
- Repository: `syaripin-i8i/mdex`
- Workflow: `.github/workflows/release.yml`

Keep the workflow on `workflow_dispatch` until tag-triggered releases are
intentionally enabled. If a protected GitHub environment is added later, update the
PyPI Trusted Publisher identity at the same time.

## Pre-release Checklist

1. Update the version in `pyproject.toml` and `mdex/__init__.py`.
2. Move the intended entries out of `Unreleased` in `CHANGELOG.md`.
3. Update the supported-version table in `SECURITY.md` for the latest `0.x` line.
4. Update `pylock.toml`:
   - `python -m pip lock -e ".[dev]" -o pylock.toml`
   - Regenerate only on an environment that yields the dependency superset
     (historically Windows + a recent supported Python). `pip lock` resolves
     the current environment only, and `install_from_pylock.py` filters by
     marker at install time, so a lock generated on macOS/Linux silently drops
     Windows-only packages (`colorama`, `pywin32-ctypes`) and can break the
     Windows CI lane.
   - Without a Windows environment, keep the committed lock and apply
     surgical version bumps, or move to a multi-environment lock generator.
   - `export_release_hashes.py` requires Python >= 3.11 (stdlib `tomllib`).
5. Update release hash catalog:
   - `python .github/scripts/export_release_hashes.py --lock pylock.toml --output .github/locks/pypi_release_hashes.json`
6. Run local verification:
   - `python -m pytest -q`
   - `python -m build --no-isolation`
   - `python -m twine check dist/*`

## Manual Release Run

Create and push an annotated version tag, then dispatch the workflow at that tag:

```bash
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z
gh workflow run release.yml --ref vX.Y.Z
```

The workflow rejects branch refs and tags that do not equal `v<project.version>`.

The workflow performs:

1. tag/package-version and release-metadata validation
2. lockfile-driven install (`python .github/scripts/install_from_pylock.py --lock pylock.toml --editable .`)
3. full test suite
4. build with hash-locked `setuptools` / `wheel` (`python -m build --no-isolation`)
5. metadata validation (`python -m twine check dist/*`)
6. immutable artifact upload before any package install smoke
7. separate dependency-free sdist smoke (`pip install --no-deps --no-build-isolation dist/*.tar.gz` then `mdex --help`)
8. separate dependency-free wheel smoke (`pip install --no-deps dist/*.whl` then `mdex --help`)
9. fresh download of the validated immutable artifact into the isolated publish job
10. Trusted Publishing upload with attestation

Only the publish job receives `id-token: write`; checkout, dependency installation,
tests, and builds run without OIDC minting permission.

## Future Automation

Tag-triggered automatic release is intentionally not enabled yet.
If enabled later, keep manual dispatch as a safe fallback.
