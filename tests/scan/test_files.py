from pathlib import Path, PurePosixPath

import pytest

from aiarb.scan import files as files_module
from aiarb.scan.files import RepoFiles
from tests.scan.repos import commit, init, make


def names(files: RepoFiles) -> list[str]:
    return [str(path) for path in files.paths]


def test_under_git_ignored_files_are_left_out_and_untracked_ones_kept(tmp_path: Path) -> None:
    init(tmp_path)
    commit(tmp_path, {".gitignore": "node_modules/\n*.log\n", "src/app.py": ""})
    make(tmp_path, {"node_modules/x/index.js": "", "debug.log": "", "src/new.py": ""})
    make(tmp_path, {".aiarb/runs/r/checkpoints.sqlite": ""})

    assert names(RepoFiles.scan(tmp_path)) == [".gitignore", "src/app.py", "src/new.py"]


def test_without_git_generated_folders_are_skipped(tmp_path: Path) -> None:
    make(
        tmp_path,
        {
            "app/main.go": "",
            "node_modules/x/index.js": "",
            ".venv/lib/site.py": "",
            "app/__pycache__/main.pyc": "",
        },
    )

    assert names(RepoFiles.scan(tmp_path)) == ["app/main.go"]


def test_files_deleted_but_still_tracked_are_left_out(tmp_path: Path) -> None:
    init(tmp_path)
    commit(tmp_path, {"a.py": "", "b.py": ""})
    (tmp_path / "b.py").unlink()

    assert names(RepoFiles.scan(tmp_path)) == ["a.py"]


def test_finding_files_by_name_and_pattern(tmp_path: Path) -> None:
    files = RepoFiles.scan(
        make(tmp_path, {"Dockerfile": "", "api/Dockerfile": "", "infra/main.tf": "", "x.tf": ""})
    )

    assert files.named("Dockerfile") == [
        PurePosixPath("Dockerfile"),
        PurePosixPath("api/Dockerfile"),
    ]
    assert files.matching("*.tf") == [PurePosixPath("infra/main.tf"), PurePosixPath("x.tf")]
    assert files.has("api/Dockerfile")
    assert not files.has("Dockerfile.dev")


def test_reading_never_follows_a_link_out_of_the_repo(tmp_path: Path) -> None:
    outside = make(tmp_path / "outside", {"secret.txt": "key"})
    repo = make(tmp_path / "repo", {"ok.txt": "fine"})
    (repo / "link.txt").symlink_to(outside / "secret.txt")
    files = RepoFiles.scan(repo)

    assert files.read("ok.txt") == "fine"
    assert files.read("link.txt") is None


def test_big_files_are_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files_module, "MAX_READ_BYTES", 10)
    files = RepoFiles.scan(make(tmp_path, {"small.txt": "tiny", "big.txt": "x" * 11}))

    assert files.read("small.txt") == "tiny"
    assert files.read("big.txt") is None


def test_a_huge_repo_is_cut_off_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files_module, "MAX_FILES", 2)

    files = RepoFiles.scan(make(tmp_path, {"a": "", "b": "", "c": ""}))

    assert names(files) == ["a", "b"]
    assert files.truncated
