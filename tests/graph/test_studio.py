"""LangGraph Studio loads the graph named in `langgraph.json` and reads its schemas.

`uv run langgraph dev` starts Studio against the real server. These tests check
the same things without it, so CI catches a graph Studio could not show.
"""

import importlib.util
import json
from pathlib import Path

from langgraph.graph.state import CompiledStateGraph

ROOT = Path(__file__).parent.parent.parent


def load_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    graphs = json.loads((ROOT / "langgraph.json").read_text())["graphs"]
    path, name = graphs["aiarb"].rsplit(":", 1)
    spec = importlib.util.spec_from_file_location("studio_entry", ROOT / path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = getattr(module, name)()
    assert isinstance(graph, CompiledStateGraph)
    return graph


def test_langgraph_json_names_a_graph_factory_that_builds() -> None:
    assert load_graph().name == "aiarb"


def test_studio_can_read_every_schema() -> None:
    graph = load_graph()

    assert "ledger" in graph.get_input_jsonschema()["properties"]
    assert "status" in graph.get_output_jsonschema()["properties"]


def test_the_context_form_offers_settings_not_ports() -> None:
    schema = load_graph().get_context_jsonschema()

    assert schema is not None
    assert set(schema["properties"]) == {"charter", "repo"}
