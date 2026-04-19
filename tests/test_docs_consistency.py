from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"
ARCHIVE_DIR = DOCS_DIR / "archive"


def _markdown_docs_outside_archive() -> list[Path]:
    docs: list[Path] = []
    for path in DOCS_DIR.rglob("*.md"):
        try:
            path.relative_to(ARCHIVE_DIR)
        except ValueError:
            docs.append(path)
    return sorted(docs)


def test_design_declares_phase_a_complete() -> None:
    design_text = (DOCS_DIR / "design.md").read_text(encoding="utf-8")
    assert "Phase A（導線）: 完了" in design_text


def test_non_archive_docs_do_not_contain_unstarted_terms() -> None:
    blocked_terms = ("未着手", "未実装")
    offenders: list[str] = []
    for path in _markdown_docs_outside_archive():
        text = path.read_text(encoding="utf-8")
        for term in blocked_terms:
            if term in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()} contains '{term}'")
    assert not offenders, f"found stale planning terms outside docs/archive: {offenders}"
