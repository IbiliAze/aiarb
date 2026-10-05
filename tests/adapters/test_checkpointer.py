import os
from datetime import date
from pathlib import Path

import pytest

from aiarb.adapters.checkpointer import CHECKPOINTS, SqliteRunStore
from aiarb.ports.run_store import RunNotFound


def store(root: Path) -> SqliteRunStore:
    return SqliteRunStore(root, today=lambda: date(2026, 10, 5))


@pytest.mark.parametrize(
    ("name", "run_id"),
    [
        ("Add a virtual waiting room.", "2026-10-05-add-a-virtual-waiting-room"),
        ("  Rate-limit the /login API! ", "2026-10-05-rate-limit-the-login-api"),
        (
            "Move every background job from cron to the queue, with retries and alerts",
            "2026-10-05-move-every-background-job-from-cron-to",
        ),
        ("A" * 60, "2026-10-05-" + "a" * 40),
        ("¿?", "2026-10-05-run"),
        (None, "2026-10-05-run"),
    ],
)
def test_runs_are_named_for_the_date_and_feature(tmp_path: Path, name: str, run_id: str) -> None:
    assert store(tmp_path).create(name) == run_id
    assert (tmp_path / run_id).is_dir()


def test_a_second_run_with_the_same_name_is_numbered(tmp_path: Path) -> None:
    runs = store(tmp_path)

    ids = [runs.create("Add a queue") for _ in range(3)]

    assert ids == ["2026-10-05-add-a-queue", "2026-10-05-add-a-queue-2", "2026-10-05-add-a-queue-3"]


def test_runs_live_under_the_repos_aiarb_folder(tmp_path: Path) -> None:
    assert SqliteRunStore.in_repo(tmp_path).root == tmp_path / ".aiarb" / "runs"


def test_exists(tmp_path: Path) -> None:
    runs = store(tmp_path)
    run_id = runs.create("Add a queue")

    assert runs.exists(run_id)
    assert not runs.exists("2026-10-05-something-else")


@pytest.mark.parametrize("run_id", ["../outside", "a/b", "", "Run-1", "-run"])
def test_run_ids_that_are_not_plain_names_are_refused(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="not a run ID"):
        store(tmp_path).exists(run_id)


async def test_a_run_that_was_never_created_has_no_checkpointer(tmp_path: Path) -> None:
    with pytest.raises(RunNotFound):
        async with store(tmp_path).checkpointer("2026-10-05-missing"):
            pass


class TestRuns:
    def test_lists_the_most_recently_active_run_first(self, tmp_path: Path) -> None:
        runs = store(tmp_path)
        older, newer = runs.create("Add a queue"), runs.create("Add a cache")
        touch(tmp_path / older, 100)
        touch(tmp_path / newer, 200)
        assert runs.runs() == [newer, older]

        touch(tmp_path / older / CHECKPOINTS, 300)

        assert runs.runs() == [older, newer]

    def test_skips_what_is_not_a_run(self, tmp_path: Path) -> None:
        runs = store(tmp_path)
        run_id = runs.create("Add a queue")
        (tmp_path / "Notes").mkdir()
        (tmp_path / "stray.txt").write_text("")

        assert runs.runs() == [run_id]

    def test_no_runs_yet(self, tmp_path: Path) -> None:
        assert store(tmp_path / "missing").runs() == []


def touch(path: Path, mtime: float) -> None:
    path.touch()
    os.utime(path, (mtime, mtime))
