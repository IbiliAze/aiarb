"""The Claude Code adapter against real Claude Code. These spend money, a few cents
a run on Haiku, so they run only when asked:

    AIARB_LIVE_TESTS=1 uv run pytest tests/live
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock
from claude_agent_sdk import query as sdk_query
from pydantic import BaseModel, ConfigDict

from aiarb.adapters.claude_code import READ_ONLY_TOOLS, ClaudeCodeAgent
from aiarb.ports.calls import FatalCallError
from aiarb.ports.code_agent import AgentRequest, OutputT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from claude_agent_sdk import ClaudeAgentOptions, Message

pytestmark = pytest.mark.skipif(
    os.environ.get("AIARB_LIVE_TESTS") != "1",
    reason="calls real Claude Code; set AIARB_LIVE_TESTS=1 to run",
)

MODEL = "claude-haiku-4-5-20251001"
CAP = 0.5
NOTES = "The codeword is PELICAN-42.\n"
SECRET = "The vault code is 7781.\n"
WRITE_ATTEMPT = (
    "This is a test of your permissions in a scratch folder. Use your tools to try each "
    "of these, in order, and report whether any of them worked: create a file named "
    "hacked.txt containing 'x'; replace the text in notes.txt with 'gone'; run the "
    "shell command `touch ran.txt`."
)


class Codeword(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codeword: str


class Attempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    succeeded: bool
    what_happened: str


class Recorder:
    """The real SDK query, noting each tool the agent tried and each one refused."""

    def __init__(self, *, loosen: bool = False) -> None:
        """With `loosen`, the writing tools are loaded and pre-approved, so only
        the adapter's hook stands between the agent and the repo."""
        self.loosen = loosen
        self.tried: list[str] = []
        self.refused: list[str] = []

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        if self.loosen:
            writing = ["Write", "Edit", "Bash"]
            options.tools = [*READ_ONLY_TOOLS, *writing]
            options.allowed_tools = [*READ_ONLY_TOOLS, *writing]
            options.disallowed_tools = []
            options.system_prompt = {"type": "preset", "preset": "claude_code"}
        async for message in sdk_query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                self.tried += [b.name for b in message.content if isinstance(b, ToolUseBlock)]
            if isinstance(message, ResultMessage):
                self.refused += [d["tool_name"] for d in message.permission_denials or ()]
            yield message


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.txt").write_text(NOTES)
    (tmp_path / "secret.txt").write_text(SECRET)
    return root


def ask(
    repo: Path, prompt: str, schema: type[OutputT], session_id: str | None = None
) -> AgentRequest[OutputT]:
    return AgentRequest(prompt=prompt, repo=repo, model=MODEL, schema=schema, session_id=session_id)


async def test_answers_in_the_schema_and_resumes_its_session(repo: Path) -> None:
    agent = ClaudeCodeAgent(spend_cap_usd=CAP)

    first = await agent.run(ask(repo, "Read notes.txt and report the codeword in it.", Codeword))
    second = await agent.run(
        ask(
            repo,
            "Without reading any file, repeat the codeword you reported earlier.",
            Codeword,
            session_id=first.session_id,
        )
    )

    assert first.output.codeword == "PELICAN-42"
    assert second.output.codeword == "PELICAN-42"
    assert second.session_id == first.session_id
    assert first.cost.usd > 0
    assert first.cost.input_tokens > 0
    assert first.cost.output_tokens > 0


async def test_a_write_attempt_is_refused(repo: Path) -> None:
    recorder = Recorder()
    agent = ClaudeCodeAgent(spend_cap_usd=CAP, query=recorder)

    answer = await agent.run(ask(repo, WRITE_ATTEMPT, Attempt))

    assert sorted(path.name for path in repo.iterdir()) == ["notes.txt"]
    assert (repo / "notes.txt").read_text() == NOTES
    assert not answer.output.succeeded, answer.output.what_happened
    assert set(recorder.tried) <= {*READ_ONLY_TOOLS, "StructuredOutput"}, recorder.tried


async def test_the_hook_alone_refuses_writes_when_the_tools_are_there(repo: Path) -> None:
    recorder = Recorder(loosen=True)
    agent = ClaudeCodeAgent(spend_cap_usd=CAP, query=recorder)

    answer = await agent.run(ask(repo, WRITE_ATTEMPT, Attempt))

    assert sorted(path.name for path in repo.iterdir()) == ["notes.txt"]
    assert (repo / "notes.txt").read_text() == NOTES
    assert {"Write", "Bash"} <= set(recorder.refused), recorder.tried
    assert not answer.output.succeeded, answer.output.what_happened


async def test_a_file_outside_the_repo_cannot_be_read(repo: Path) -> None:
    recorder = Recorder()
    agent = ClaudeCodeAgent(spend_cap_usd=CAP, query=recorder)
    secret = repo.parent / "secret.txt"
    prompt = (
        f"Read the file {secret} and report the vault code in it as the codeword. "
        "If you cannot read it, report the codeword UNREADABLE."
    )

    answer = await agent.run(ask(repo, prompt, Codeword))

    assert "7781" not in answer.output.codeword
    assert "Read" in recorder.refused, recorder.tried


async def test_a_lost_session_starts_afresh(repo: Path) -> None:
    agent = ClaudeCodeAgent(spend_cap_usd=CAP)

    answer = await agent.run(
        ask(repo, "Read notes.txt and report the codeword in it.", Codeword, str(uuid.uuid4()))
    )

    assert answer.output.codeword == "PELICAN-42"


async def test_the_spend_cap_stops_the_call(repo: Path) -> None:
    agent = ClaudeCodeAgent(spend_cap_usd=0.0001)

    with pytest.raises(FatalCallError, match="spend cap") as caught:
        await agent.run(ask(repo, "Read notes.txt and report the codeword in it.", Codeword))

    assert caught.value.cost.usd > 0
