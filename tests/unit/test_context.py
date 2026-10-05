from pathlib import Path
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from aiarb.config.charter import Charter
from aiarb.graph.context import RunContext
from aiarb.graph.state import RunState, initial_state
from tests.fakes import ScriptedCodeAgent, ScriptedScorer


def context(repo: Path, charter: Charter | None = None) -> RunContext:
    return RunContext(
        charter=charter or Charter(),
        repo=repo,
        agent=ScriptedCodeAgent([]),
        scorer=ScriptedScorer([]),
    )


def test_the_repo_is_stored_as_an_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    monkeypatch.chdir(tmp_path)

    assert context(Path("app")).repo == tmp_path.resolve() / "app"


@pytest.mark.parametrize("name", ["missing", "a-file.txt"])
def test_the_repo_must_be_a_directory(tmp_path: Path, name: str) -> None:
    (tmp_path / "a-file.txt").write_text("")

    with pytest.raises(ValueError, match="must be a directory"):
        context(tmp_path / name)


async def test_nodes_reach_the_context_through_the_runtime(tmp_path: Path) -> None:
    charter = Charter.model_validate({"debate": {"max_rounds": 7}})
    seen: dict[str, Any] = {}

    def node(state: RunState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        seen["max_rounds"] = runtime.context.charter.debate.max_rounds
        seen["repo"] = runtime.context.repo
        return {}

    graph = StateGraph(RunState, context_schema=RunContext)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)

    await graph.compile().ainvoke(initial_state(), context=context(tmp_path, charter))

    assert seen == {"max_rounds": 7, "repo": tmp_path.resolve()}
