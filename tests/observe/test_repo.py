import os
from pathlib import Path

import pytest

from aiarb.observe.repo import RepoSnapshot
from tests.scan import repos


@pytest.fixture(params=["plain", "git"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    root = repos.make(tmp_path, {"app.py": "print('hi')\n", "docs/notes.md": "# Notes\n"})
    if request.param == "git":
        repos.init(root)
        repos.commit(root, {}, "Start")
    return root


def test_an_untouched_repo_has_no_changes(repo: Path) -> None:
    assert RepoSnapshot.take(repo).changes(RepoSnapshot.take(repo)) == []


def test_finds_files_added_removed_and_changed(repo: Path) -> None:
    before = RepoSnapshot.take(repo)
    (repo / "new.py").write_text("x = 1\n")
    (repo / "docs" / "notes.md").unlink()
    (repo / "app.py").write_text("print('changed')\n")

    assert before.changes(RepoSnapshot.take(repo)) == ["app.py", "docs/notes.md", "new.py"]


def test_finds_a_file_rewritten_at_the_same_size(repo: Path) -> None:
    before = RepoSnapshot.take(repo)
    stat = (repo / "app.py").stat()
    (repo / "app.py").write_text("print('ho')\n")
    os.utime(repo / "app.py", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert before.changes(RepoSnapshot.take(repo)) == ["app.py"]


def test_leaves_out_aiarbs_own_folder(repo: Path) -> None:
    before = RepoSnapshot.take(repo)
    (repo / ".aiarb" / "runs").mkdir(parents=True)
    (repo / ".aiarb" / "runs" / "log.jsonl").write_text("{}\n")

    assert before.changes(RepoSnapshot.take(repo)) == []
