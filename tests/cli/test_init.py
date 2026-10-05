from pathlib import Path

import pytest
import yaml

from aiarb.cli.init import IGNORED, ignore_runs, starter_charter
from aiarb.config.charter import Charter, load_charter
from tests.cli.helpers import aiarb


def test_writes_a_charter_with_every_default(tmp_path: Path) -> None:
    outcome = aiarb(tmp_path, "init", "--llm")

    assert outcome.code == 0
    assert load_charter(tmp_path) == Charter()
    assert "Wrote .aiarb/charter.yaml, scoring with a language model." in outcome.stderr


def test_keeps_runs_and_the_cache_out_of_git(tmp_path: Path) -> None:
    aiarb(tmp_path, "init")

    assert (tmp_path / ".aiarb" / ".gitignore").read_text() == IGNORED
    assert {"runs/", "cache/"} <= set(IGNORED.splitlines())


def test_scores_with_jev_when_asked(tmp_path: Path) -> None:
    aiarb(tmp_path, "init", "--jev")

    assert load_charter(tmp_path).scoring.provider == "jev"


@pytest.mark.parametrize(("key", "provider"), [("tsk-123", "jev"), ("", "llm")])
def test_by_default_scores_with_jev_when_there_is_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, provider: str
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", key)

    aiarb(tmp_path, "init")

    assert load_charter(tmp_path).scoring.provider == provider


def test_leaves_a_charter_that_is_there(tmp_path: Path) -> None:
    charter = tmp_path / ".aiarb" / "charter.yaml"
    charter.parent.mkdir()
    charter.write_text("debate:\n  max_rounds: 2\n")

    outcome = aiarb(tmp_path, "init")

    assert outcome.code == 4
    assert "exists already: pass --force to replace it" in outcome.stderr
    assert charter.read_text() == "debate:\n  max_rounds: 2\n"
    assert aiarb(tmp_path, "init", "--force", "--llm").code == 0
    assert load_charter(tmp_path) == Charter()


def test_leaves_a_gitignore_that_is_there(tmp_path: Path) -> None:
    ignore = tmp_path / ".aiarb" / ".gitignore"
    ignore.parent.mkdir()
    ignore.write_text("runs/\n")

    ignore_runs(tmp_path)

    assert ignore.read_text() == "runs/\n"


def test_the_starter_charter_explains_each_setting() -> None:
    text = starter_charter()

    assert "planner: claude-opus-5-5" in text
    assert "# must differ from the planner" in text
    assert "# data_integrity: 5" in text
    assert yaml.safe_load(text)["priorities"] is None
