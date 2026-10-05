"""The fact sheet for each fixture repo, and its cache.

Each fixture repo's sheet is compared with `tests/fixtures/facts/<name>.md`.
After a deliberate change to what the scan reports, rewrite those files with
`AIARB_UPDATE_FACTS=1 uv run pytest tests/scan` and review the diff.
"""

import os
import shutil
from pathlib import Path

import pytest

from aiarb.scan import facts
from aiarb.scan.facts import CACHE_PATH, SCAN_VERSION, build, fact_sheet
from tests.scan.repos import commit, init, make

FIXTURES = Path(__file__).parent.parent / "fixtures"
REPOS = sorted(path.name for path in (FIXTURES / "repos").iterdir() if path.is_dir())


def copy(name: str, tmp_path: Path) -> Path:
    """A fixture repo copied out of this one, so it is not under aiarb's git."""
    return Path(shutil.copytree(FIXTURES / "repos" / name, tmp_path / name))


def test_there_are_fixture_repos_of_different_stacks() -> None:
    assert REPOS == ["bare-scripts", "go-service", "node-web", "python-api"]


@pytest.mark.parametrize("name", REPOS)
def test_each_fixture_repos_fact_sheet(name: str, tmp_path: Path) -> None:
    sheet = build(copy(name, tmp_path))

    expected = FIXTURES / "facts" / f"{name}.md"
    if os.environ.get("AIARB_UPDATE_FACTS"):
        expected.write_text(sheet)
    assert sheet == expected.read_text()


def cache_files(repo: Path) -> list[str]:
    folder = repo / CACHE_PATH
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def committed(name: str, tmp_path: Path) -> Path:
    repo = init(copy(name, tmp_path))
    commit(repo, {}, "Start")
    return repo


def head(repo: Path) -> str:
    return (repo / ".git" / "refs" / "heads" / "main").read_text().strip()


class TestUnderGit:
    def test_the_sheet_names_the_commit_and_includes_recent_changes(self, tmp_path: Path) -> None:
        repo = committed("go-service", tmp_path)

        sheet = fact_sheet(repo)

        assert f"Scanned at commit {head(repo)[:12]}.\n" in sheet
        assert "## Recent changes\n\n- 1 commit in the last 90 days, the latest on " in sheet

    def test_a_scanned_commit_is_read_from_the_cache(self, tmp_path: Path) -> None:
        repo = committed("go-service", tmp_path)
        first = fact_sheet(repo)

        assert cache_files(repo) == [f"{head(repo)}-v{SCAN_VERSION}.md"]
        cached = repo / CACHE_PATH / cache_files(repo)[0]
        cached.write_text(first + "From the cache.\n")

        assert fact_sheet(repo).endswith("From the cache.\n")

    def test_a_new_commit_is_scanned_afresh(self, tmp_path: Path) -> None:
        repo = committed("go-service", tmp_path)
        fact_sheet(repo)

        commit(repo, {"internal/queue/limits.go": "package queue\n"})

        assert "Go (4 files)" in fact_sheet(repo)
        assert len(cache_files(repo)) == 2

    def test_uncommitted_changes_are_scanned_and_not_cached(self, tmp_path: Path) -> None:
        repo = committed("go-service", tmp_path)
        make(repo, {"internal/queue/limits.go": "package queue\n"})

        sheet = fact_sheet(repo)

        assert "with uncommitted changes on top." in sheet
        assert "Go (4 files)" in sheet
        assert cache_files(repo) == []

    def test_a_sheet_from_an_older_scan_is_not_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = committed("go-service", tmp_path)
        fact_sheet(repo)

        monkeypatch.setattr(facts, "SCAN_VERSION", SCAN_VERSION + 1)
        fact_sheet(repo)

        assert cache_files(repo) == [
            f"{head(repo)}-v{SCAN_VERSION}.md",
            f"{head(repo)}-v{SCAN_VERSION + 1}.md",
        ]

    def test_a_cache_that_cannot_be_written_is_skipped(self, tmp_path: Path) -> None:
        repo = committed("go-service", tmp_path)
        blocked = make(tmp_path, {"not-a-folder": ""}) / "not-a-folder"

        sheet = fact_sheet(repo, cache=blocked)

        assert sheet.startswith("# Repo facts\n")

    def test_only_the_cache_is_written_and_only_inside_aiarb(self, tmp_path: Path) -> None:
        repo = committed("python-api", tmp_path)
        before = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts)

        fact_sheet(repo)

        after = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts)
        assert [path for path in after if path not in before] == [
            ".aiarb",
            ".aiarb/cache",
            ".aiarb/cache/facts",
            f".aiarb/cache/facts/{head(repo)}-v{SCAN_VERSION}.md",
        ]


def test_a_repo_without_git_is_scanned_without_a_cache(tmp_path: Path) -> None:
    repo = copy("bare-scripts", tmp_path)

    assert fact_sheet(repo) == build(repo)
    assert not (repo / ".aiarb").exists()


def test_an_empty_repo_says_so(tmp_path: Path) -> None:
    sheet = build(tmp_path)

    assert "## Tests\n\n- No test files found, against 0 source files.\n" in sheet
