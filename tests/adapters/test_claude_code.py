from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    Message,
    ProcessError,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
)
from claude_agent_sdk import query as sdk_query
from pydantic import BaseModel, ConfigDict, Field

from aiarb.adapters.claude_code import (
    INVALID_SCHEMA,
    LOST_SESSION,
    READ_ONLY_TOOLS,
    ClaudeCodeAgent,
    answer_schema,
    read_only_hook,
    refusal,
)
from aiarb.adapters.llm_scorer import _choice_among
from aiarb.config.charter import Charter
from aiarb.ports.calls import CallError, Cost, FatalCallError, RetryableCallError
from aiarb.ports.code_agent import AgentRequest, CodeAgent
from aiarb.schemas import outputs

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from claude_agent_sdk import ClaudeAgentOptions, HookContext


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    lines: int = Field(ge=0)


ANSWER = {"answer": "In src/queue.py.", "lines": 12}
USAGE = {
    "input_tokens": 100,
    "cache_creation_input_tokens": 20,
    "cache_read_input_tokens": 300,
    "output_tokens": 50,
}


def result(**overrides: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1000,
        "duration_api_ms": 900,
        "is_error": False,
        "num_turns": 3,
        "session_id": "session-1",
        "total_cost_usd": 0.42,
        "usage": USAGE,
        "structured_output": ANSWER,
    }
    return ResultMessage(**(fields | overrides))


def failed(subtype: str = "success", **overrides: Any) -> tuple[ResultMessage, ResultError]:
    """A failed result as Claude Code reports it: yielded, then raised as the process exits."""
    message = result(subtype=subtype, is_error=True, structured_output=None, **overrides)
    data = {
        "type": "result",
        "subtype": subtype,
        "is_error": True,
        "session_id": message.session_id,
        "total_cost_usd": message.total_cost_usd,
        "usage": message.usage,
        "errors": message.errors or [],
        "result": message.result,
        "api_error_status": message.api_error_status,
    }
    return message, ResultError("Claude Code returned an error result", data, exit_code=1)


def failing(subtype: str = "success", **overrides: Any) -> Session:
    message, error = failed(subtype, **overrides)
    return Session([message], error)


def trouble(error: Any) -> AssistantMessage:
    return AssistantMessage(content=[], model="claude-sonnet-5-5", error=error)


@dataclass
class Session:
    messages: list[Message] = field(default_factory=list)
    error: Exception | None = None


class FakeQuery:
    """Stands in for the SDK's `query`: plays back one scripted session per call."""

    def __init__(self, *sessions: Session) -> None:
        self.sessions = list(sessions)
        self.calls: list[tuple[str, ClaudeAgentOptions]] = []

    def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        self.calls.append((prompt, options))
        return self._play(self.sessions.pop(0))

    async def _play(self, session: Session) -> AsyncIterator[Message]:
        for message in session.messages:
            yield message
        if session.error is not None:
            raise session.error

    @property
    def options(self) -> ClaudeAgentOptions:
        return self.calls[-1][1]


def agent(*sessions: Session) -> tuple[ClaudeCodeAgent, FakeQuery]:
    fake = FakeQuery(*sessions)
    return ClaudeCodeAgent(spend_cap_usd=10, query=fake), fake


def request(repo: Path, session_id: str | None = None) -> AgentRequest[Finding]:
    return AgentRequest(
        prompt="Where is the queue?",
        repo=repo,
        model="claude-sonnet-5-5",
        schema=Finding,
        session_id=session_id,
    )


def test_it_is_a_code_agent() -> None:
    assert isinstance(ClaudeCodeAgent(spend_cap_usd=1), CodeAgent)


def test_the_spend_cap_comes_from_the_charter() -> None:
    charter = Charter.model_validate({"budget": {"per_call": 2.5}})

    assert ClaudeCodeAgent.from_charter(charter).spend_cap_usd == 2.5


@pytest.mark.parametrize("cap", [0, -1])
def test_the_spend_cap_must_be_positive(cap: float) -> None:
    with pytest.raises(ValueError, match="spend cap must be positive"):
        ClaudeCodeAgent(spend_cap_usd=cap)


class TestTheSession:
    async def test_runs_in_the_repo_on_the_requested_model(self, tmp_path: Path) -> None:
        code, fake = agent(Session([result()]))

        await code.run(request(tmp_path))

        prompt, options = fake.calls[0]
        assert prompt == "Where is the queue?"
        assert options.cwd == tmp_path.resolve()
        assert options.model == "claude-sonnet-5-5"
        assert options.verbatim_prompts

    async def test_has_only_the_read_and_search_tools(self, tmp_path: Path) -> None:
        code, fake = agent(Session([result()]))

        await code.run(request(tmp_path))

        options = fake.options
        assert options.tools == ["Read", "Grep", "Glob"]
        assert options.allowed_tools == ["Read", "Grep", "Glob"]
        assert {"Bash", "Edit", "Write", "NotebookEdit"} <= set(options.disallowed_tools)
        assert options.permission_mode == "dontAsk"
        assert options.hooks is not None
        assert [matcher.matcher for matcher in options.hooks["PreToolUse"]] == [None]

    async def test_loads_no_mcp_servers_and_none_of_the_repos_settings(
        self, tmp_path: Path
    ) -> None:
        code, fake = agent(Session([result()]))

        await code.run(request(tmp_path))

        assert fake.options.mcp_servers == {}
        assert fake.options.strict_mcp_config
        assert fake.options.setting_sources == ["user"]

    async def test_carries_the_spend_cap_and_the_answers_schema(self, tmp_path: Path) -> None:
        code, fake = agent(Session([result()]))

        await code.run(request(tmp_path))

        assert fake.options.max_budget_usd == 10
        assert fake.options.output_format == {
            "type": "json_schema",
            "schema": answer_schema(Finding),
        }

    async def test_a_first_call_starts_a_session_and_a_later_one_resumes_it(
        self, tmp_path: Path
    ) -> None:
        code, fake = agent(
            Session([result(session_id="session-1")]), Session([result(session_id="session-1")])
        )

        first = await code.run(request(tmp_path))
        second = await code.run(request(tmp_path, session_id=first.session_id))

        assert fake.calls[0][1].resume is None
        assert fake.calls[1][1].resume == "session-1"
        assert second.session_id == "session-1"

    async def test_a_session_that_is_gone_is_started_afresh(self, tmp_path: Path) -> None:
        message, error = failed(
            "error_during_execution",
            session_id="session-old",
            total_cost_usd=0,
            errors=[f"{LOST_SESSION}: session-old"],
        )
        code, fake = agent(Session([message], error), Session([result(session_id="session-new")]))

        answer = await code.run(request(tmp_path, session_id="session-old"))

        assert [options.resume for _, options in fake.calls] == ["session-old", None]
        assert answer.session_id == "session-new"


class TestTheAnswer:
    async def test_is_validated_against_the_schema(self, tmp_path: Path) -> None:
        code, _ = agent(Session([result()]))

        answer = await code.run(request(tmp_path))

        assert answer.output == Finding(answer="In src/queue.py.", lines=12)
        assert answer.session_id == "session-1"

    async def test_reports_the_cost_and_every_input_token(self, tmp_path: Path) -> None:
        code, _ = agent(Session([result()]))

        answer = await code.run(request(tmp_path))

        assert answer.cost == Cost(usd=0.42, input_tokens=420, output_tokens=50)

    async def test_that_does_not_fit_the_schema_can_be_retried(self, tmp_path: Path) -> None:
        code, _ = agent(Session([result(structured_output={"answer": "Here.", "lines": -3})]))

        with pytest.raises(RetryableCallError, match=r"does not fit Finding \(lines: ") as caught:
            await code.run(request(tmp_path))

        assert caught.value.cost.usd == 0.42

    async def test_that_is_missing_can_be_retried(self, tmp_path: Path) -> None:
        code, _ = agent(Session([result(structured_output=None)]))

        with pytest.raises(RetryableCallError, match="without the structured output Finding"):
            await code.run(request(tmp_path))

    async def test_that_never_arrives_can_be_retried(self, tmp_path: Path) -> None:
        code, _ = agent(Session([]))

        with pytest.raises(RetryableCallError, match="without a result"):
            await code.run(request(tmp_path))


async def test_reaching_the_spend_cap_is_fatal_and_keeps_the_cost(tmp_path: Path) -> None:
    code, _ = agent(failing("error_max_budget_usd", total_cost_usd=10.31))

    with pytest.raises(FatalCallError, match=r"spend cap of \$10\.00 .* after spending \$10\.31"):
        await code.run(request(tmp_path))


RESETS_AT = 1_791_000_000


def rejected() -> RateLimitEvent:
    info = RateLimitInfo(status="rejected", resets_at=RESETS_AT, rate_limit_type="five_hour")
    return RateLimitEvent(rate_limit_info=info, uuid="event-1", session_id="session-1")


def api_error(status: int, text: str) -> Session:
    return failing(api_error_status=status, result=f"API Error: {status} {text}")


@pytest.mark.parametrize(
    ("session", "kind", "match"),
    [
        pytest.param(
            api_error(429, "rate_limit_error"), RetryableCallError, "rate limited", id="429"
        ),
        pytest.param(api_error(529, "overloaded"), RetryableCallError, "overloaded", id="529"),
        pytest.param(api_error(500, "internal"), RetryableCallError, "internal", id="500"),
        pytest.param(
            api_error(401, "invalid x-api-key"), FatalCallError, "not signed in", id="401"
        ),
        pytest.param(api_error(402, "credit"), FatalCallError, "billing problem", id="402"),
        pytest.param(api_error(404, "model: claude-x"), FatalCallError, "claude-x", id="404"),
        pytest.param(
            failing("error_max_structured_output_retries", errors=["bad shape"]),
            RetryableCallError,
            "required shape: bad shape",
            id="structured-output-retries",
        ),
        pytest.param(
            failing("error_during_execution", errors=["tool crashed"]),
            RetryableCallError,
            "tool crashed",
            id="during-execution",
        ),
        pytest.param(
            Session([trouble("authentication_failed")], ProcessError("exit", exit_code=1)),
            FatalCallError,
            "not signed in",
            id="auth-then-exit",
        ),
        pytest.param(
            Session([trouble("billing_error")], ProcessError("exit", exit_code=1)),
            FatalCallError,
            "billing",
            id="billing-then-exit",
        ),
        pytest.param(
            Session([trouble("invalid_request")], ProcessError("exit", exit_code=1)),
            FatalCallError,
            "refused",
            id="invalid-request-then-exit",
        ),
        pytest.param(
            Session([trouble("rate_limit")], ProcessError("exit", exit_code=1)),
            RetryableCallError,
            "rate limited",
            id="rate-limit-then-exit",
        ),
        pytest.param(
            Session([trouble("server_error")], ProcessError("exit", exit_code=1)),
            RetryableCallError,
            "stopped unexpectedly",
            id="server-error-then-exit",
        ),
        pytest.param(
            Session([], ProcessError("killed", exit_code=137)),
            RetryableCallError,
            "exit code: 137",
            id="process-killed",
        ),
        pytest.param(
            Session([], CLIConnectionError("pipe closed")),
            RetryableCallError,
            "pipe closed",
            id="connection-lost",
        ),
        pytest.param(
            Session([], CLIJSONDecodeError("{oops", ValueError("bad"))),
            RetryableCallError,
            "decode JSON",
            id="garbled-output",
        ),
    ],
)
async def test_failures_are_sorted_into_retryable_and_fatal(
    tmp_path: Path, session: Session, kind: type[CallError], match: str
) -> None:
    code, _ = agent(session)

    with pytest.raises(CallError, match=match) as caught:
        await code.run(request(tmp_path))

    assert type(caught.value) is kind


async def test_a_failed_call_keeps_what_it_spent(tmp_path: Path) -> None:
    code, _ = agent(failing("error_during_execution", total_cost_usd=1.5))

    with pytest.raises(RetryableCallError) as caught:
        await code.run(request(tmp_path))

    assert caught.value.cost == Cost(usd=1.5, input_tokens=420, output_tokens=50)


async def test_a_failure_raised_without_its_result_still_keeps_the_cost(tmp_path: Path) -> None:
    _, error = failed("error_during_execution", total_cost_usd=0.75)
    code, _ = agent(Session([], error))

    with pytest.raises(RetryableCallError) as caught:
        await code.run(request(tmp_path))

    assert caught.value.cost.usd == 0.75


async def test_a_lost_session_on_a_new_call_is_not_retried_fresh(tmp_path: Path) -> None:
    code, fake = agent(failing("error_during_execution", errors=[LOST_SESSION]))

    with pytest.raises(RetryableCallError):
        await code.run(request(tmp_path))

    assert len(fake.calls) == 1


async def test_a_usage_limit_that_lifts_later_is_fatal_and_says_when(tmp_path: Path) -> None:
    message, error = failed(api_error_status=429, result="API Error: usage limit")
    code, _ = agent(Session([rejected(), trouble("rate_limit"), message], error))
    resets = datetime.fromtimestamp(RESETS_AT).strftime("%Y-%m-%d %H:%M")

    with pytest.raises(FatalCallError, match=f"usage limit is reached until {resets}"):
        await code.run(request(tmp_path))


class TestAMissingInstall:
    async def test_is_fatal_and_says_how_to_install(self, tmp_path: Path) -> None:
        code, _ = agent(Session([], CLINotFoundError()))

        with pytest.raises(FatalCallError, match=r"^Claude Code was not found\. .* install it"):
            await code.run(request(tmp_path))

    async def test_at_a_configured_path_names_the_path(self, tmp_path: Path) -> None:
        """Through the real SDK: no process starts, so this costs nothing."""
        missing = tmp_path / "no-such-claude"
        code = ClaudeCodeAgent(spend_cap_usd=1, cli_path=missing)

        with pytest.raises(FatalCallError, match=f"not found at {missing}"):
            await code.run(request(tmp_path))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "secret.txt").write_text("hunter2\n")
    return root.resolve()


class TestTheReadOnlyGate:
    @pytest.mark.parametrize("tool", READ_ONLY_TOOLS)
    def test_lets_the_read_tools_run_inside_the_repo(self, repo: Path, tool: str) -> None:
        assert refusal(repo, tool, {"path": "src", "pattern": "**/*.py"}) is None
        assert refusal(repo, tool, {"file_path": str(repo / "src" / "app.py")}) is None
        assert refusal(repo, tool, {"pattern": "def "}) is None

    @pytest.mark.parametrize(
        "tool", ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch", "Agent"]
    )
    def test_refuses_every_other_tool(self, repo: Path, tool: str) -> None:
        problem = refusal(repo, tool, {"file_path": "src/app.py", "content": "x"})

        assert (
            problem
            == f"{tool} is not available: this review may only read and search the repository."
        )

    @pytest.mark.parametrize(
        "tool_input",
        [
            {"file_path": "../secret.txt"},
            {"file_path": "/etc/passwd"},
            {"file_path": "~/.ssh/id_ed25519"},
            {"path": ".."},
            {"path": "/"},
            {"file_path": 7},
        ],
    )
    def test_refuses_paths_outside_the_repo(self, repo: Path, tool_input: dict[str, Any]) -> None:
        problem = refusal(repo, "Read", tool_input)

        assert problem is not None
        assert "outside the repository" in problem

    def test_refuses_a_link_that_leads_out_of_the_repo(self, repo: Path) -> None:
        (repo / "notes.txt").symlink_to(repo.parent / "secret.txt")

        assert refusal(repo, "Read", {"file_path": "notes.txt"}) is not None

    @pytest.mark.parametrize("pattern", ["/etc/*", "../**/*.txt", "~/**", "src/../../*"])
    def test_refuses_a_glob_that_reaches_out_of_the_repo(self, repo: Path, pattern: str) -> None:
        assert refusal(repo, "Glob", {"pattern": pattern}) is not None

    async def test_as_a_hook_denies_a_write(self, repo: Path) -> None:
        hook = read_only_hook(repo)
        context: HookContext = {"signal": None}

        decision = await hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-1",
                "transcript_path": "/dev/null",
                "cwd": str(repo),
                "tool_name": "Write",
                "tool_input": {"file_path": "src/app.py", "content": "x"},
                "tool_use_id": "tool-1",
            },
            "tool-1",
            context,
        )

        assert decision == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Write is not available: this review may only read and search the repository."
                ),
            }
        }

    async def test_as_a_hook_lets_a_read_through(self, repo: Path) -> None:
        hook = read_only_hook(repo)
        context: HookContext = {"signal": None}

        decision = await hook(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-1",
                "transcript_path": "/dev/null",
                "cwd": str(repo),
                "tool_name": "Read",
                "tool_input": {"file_path": "src/app.py"},
                "tool_use_id": "tool-1",
            },
            "tool-1",
            context,
        )

        assert decision == {}


class Cited(BaseModel):
    discriminator: str
    """A field that happens to share the keyword's name."""
    citations: list[outputs.Citation]


class TestTheAnswerSchema:
    def test_leaves_out_the_keywords_claude_code_refuses(self) -> None:
        schema = answer_schema(Cited)

        assert "discriminator" not in str(schema["$defs"])
        assert "discriminator" not in str(schema["properties"]["citations"])
        assert schema["properties"]["citations"]["items"]["oneOf"] == [
            {"$ref": "#/$defs/CodeCitation"},
            {"$ref": "#/$defs/PlanCitation"},
        ]

    def test_keeps_a_field_with_the_same_name(self) -> None:
        schema = answer_schema(Cited)

        assert schema["properties"]["discriminator"] == {"title": "Discriminator", "type": "string"}
        assert "discriminator" in schema["required"]

    async def test_that_claude_code_refuses_is_fatal(self, tmp_path: Path) -> None:
        refused = ProcessError(
            "Command failed with exit code 1",
            exit_code=1,
            stderr=f'Error: --json-schema {INVALID_SCHEMA}: strict mode: unknown keyword: "x"',
        )
        code, _ = agent(Session([], refused))

        with pytest.raises(FatalCallError, match="cannot use the answer schema Finding"):
            await code.run(request(tmp_path))


ANSWER_SCHEMAS = [
    *(
        model
        for _, model in inspect.getmembers(outputs, inspect.isclass)
        if issubclass(model, outputs._Output) and model is not outputs._Output
    ),
    _choice_among(("factual", "design", "trade_off")),
]
"""Every schema an agent answers with, including a choice among given options."""


async def test_claude_code_accepts_every_answer_schema(tmp_path: Path) -> None:
    """Through the real Claude Code, at no cost: resuming a session that does not
    exist fails before any model is called, but only after the schema is checked."""

    async def problem(schema: type[BaseModel]) -> str:
        code = ClaudeCodeAgent(spend_cap_usd=1)
        options = code.options(
            AgentRequest(prompt="Hi.", repo=tmp_path, model="claude-haiku-4-5", schema=schema),
            resume=str(uuid.uuid4()),
        )
        lines: list[str] = []
        options.stderr = lines.append
        try:
            async for _ in sdk_query(prompt="Hi.", options=options):
                pass
        except ProcessError as error:
            output = "\n".join([str(error), *lines])
            return "" if LOST_SESSION in output else f"{schema.__name__}: {output}"
        return f"{schema.__name__}: the session was resumed"

    problems = await asyncio.gather(*(problem(schema) for schema in ANSWER_SCHEMAS))

    assert [problem for problem in problems if problem] == []
