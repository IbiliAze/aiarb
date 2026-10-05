"""The `CodeAgent` port on Claude Code, through the Claude Agent SDK.

Each call is one Claude Code session, run in the target repo. The agent may read
and search the repo and nothing else, which is enforced three ways:

- Only the Read, Grep and Glob tools are loaded, so no other tool exists for the
  model to call. Anything else is denied without asking.
- A hook checks every tool call before it runs. It refuses any other tool, bar
  the one Claude Code uses to hand back the answer, and any path outside the
  repo, symbolic links included.
- No MCP servers are loaded, and neither are the target repo's own Claude Code
  settings, so the repo under review cannot add tools or hooks. The user's own
  settings are kept, since they may hold how Claude Code signs in.

The answer comes back as structured output against the request's schema, and is
validated by the schema before it is returned. A request with a session ID
resumes that session, so the agent keeps what it read in earlier rounds. If the
session is gone, as when Claude Code has cleaned up old transcripts, the call
starts a new one: the agent rereads what it needs rather than failing the run.
"""

from __future__ import annotations

import contextlib
from collections import deque
from datetime import datetime
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Protocol, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLINotFoundError,
    HookMatcher,
    RateLimitEvent,
    ResultError,
    ResultMessage,
)
from claude_agent_sdk import query as sdk_query
from pydantic import BaseModel, ValidationError

import aiarb
from aiarb.ports.calls import (
    CallError,
    Cost,
    FatalCallError,
    RetryableCallError,
    SpendCapReached,
)
from aiarb.ports.code_agent import AgentRequest, AgentResult, OutputT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput, Message

    from aiarb.config.charter import Charter

READ_ONLY_TOOLS = ("Read", "Grep", "Glob")
"""The only tools an agent is given to look at the repo."""

ANSWER_TOOL = "StructuredOutput"
"""The tool Claude Code gives the agent for its final answer. It changes nothing."""

WRITING_TOOLS = ("Bash", "Edit", "MultiEdit", "Write", "NotebookEdit")
"""Named as disallowed as well, in case a Claude Code release loads them anyway."""

MISSING_INSTALL = (
    "Claude Code was not found{where}. aiarb runs its agents in Claude Code: install it "
    "(https://docs.claude.com/en/docs/claude-code/setup), check that `claude` runs from "
    "your shell, and run it once to sign in."
)

SIGN_IN = (
    "Claude Code is not signed in, or its credentials were refused. Run `claude` and "
    "sign in with /login, or set ANTHROPIC_API_KEY."
)

LOST_SESSION = "No conversation found with session ID"
"""How Claude Code reports a session it no longer has."""

INVALID_SCHEMA = "is not a valid JSON Schema"
"""How Claude Code reports an answer schema it cannot use, which no retry fixes."""

_ANNOTATIONS = frozenset({"discriminator"})
"""Keywords pydantic writes into a JSON schema that Claude Code's strict schema check
refuses. They only describe the schema, so leaving them out changes nothing it
accepts; pydantic still validates the answer against the full model."""

SYSTEM_PROMPT_APPEND = (
    "You are advising on this repository, not changing it. You can read and search "
    "files inside it, and nothing else: you cannot edit files, run commands, or read "
    "outside the repository. Give your final answer as the structured output asked for."
)

_RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_STDERR_LINES = 20


class Query(Protocol):
    """The SDK's `query` function, or a stand-in for it in tests."""

    def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]: ...


class ClaudeCodeAgent:
    """A `CodeAgent` that runs each call as a read-only Claude Code session."""

    def __init__(
        self,
        *,
        spend_cap_usd: float,
        cli_path: str | Path | None = None,
        query: Query = sdk_query,
    ) -> None:
        """`spend_cap_usd` is the most one call may spend. `cli_path` is the
        Claude Code executable, by default the one bundled with the SDK or found
        on the PATH."""
        if spend_cap_usd <= 0:
            raise ValueError(f"the spend cap must be positive, got {spend_cap_usd}")
        self.spend_cap_usd = spend_cap_usd
        self.cli_path = cli_path
        self._query = query

    @classmethod
    def from_charter(cls, charter: Charter, **kwargs: Any) -> ClaudeCodeAgent:
        return cls(spend_cap_usd=charter.budget.per_call, **kwargs)

    async def run(self, request: AgentRequest[OutputT]) -> AgentResult[OutputT]:
        try:
            return await self._run(request, resume=request.session_id)
        except _LostSessionError:
            return await self._run(request, resume=None)

    def options(self, request: AgentRequest[Any], *, resume: str | None) -> ClaudeAgentOptions:
        """The session's settings: read-only, capped, answering in the request's schema."""
        repo = request.repo.resolve()
        return ClaudeAgentOptions(
            cwd=repo,
            model=request.model,
            resume=resume,
            tools=list(READ_ONLY_TOOLS),
            allowed_tools=list(READ_ONLY_TOOLS),
            disallowed_tools=list(WRITING_TOOLS),
            permission_mode="dontAsk",
            hooks={"PreToolUse": [HookMatcher(hooks=[read_only_hook(repo)])]},
            setting_sources=["user"],
            mcp_servers={},
            strict_mcp_config=True,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": SYSTEM_PROMPT_APPEND,
            },
            output_format={"type": "json_schema", "schema": answer_schema(request.schema)},
            max_budget_usd=self.cap_for(request),
            verbatim_prompts=True,
            cli_path=self.cli_path,
            env={"CLAUDE_AGENT_SDK_CLIENT_APP": f"aiarb/{aiarb.__version__}"},
        )

    def cap_for(self, request: AgentRequest[Any]) -> float:
        """The most the call may spend: the agent's cap, or the request's if lower."""
        if request.spend_cap_usd is None:
            return self.spend_cap_usd
        return min(self.spend_cap_usd, request.spend_cap_usd)

    async def _run(
        self, request: AgentRequest[OutputT], *, resume: str | None
    ) -> AgentResult[OutputT]:
        options = self.options(request, resume=resume)
        stderr: deque[str] = deque(maxlen=_STDERR_LINES)
        options.stderr = stderr.append
        cap = self.cap_for(request)
        # When the two caps are the same, the run's budget is the one to raise.
        by_request = (
            request.spend_cap_usd is not None and request.spend_cap_usd <= self.spend_cap_usd
        )
        call = _Call(cap, by_request=by_request, resumed=resume is not None)
        try:
            async for message in self._query(prompt=request.prompt, options=options):
                call.see(message)
        except CLINotFoundError as error:
            where = f" at {self.cli_path}" if self.cli_path else ""
            raise FatalCallError(MISSING_INSTALL.format(where=where)) from error
        except ResultError as error:
            raise call.failure(call.result or _result_from(error)) from error
        except ClaudeSDKError as error:
            if call.result is not None and call.result.is_error:
                raise call.failure(call.result) from error
            detail = "\n".join([str(error), *stderr]).strip()
            if INVALID_SCHEMA in detail:
                raise FatalCallError(
                    f"Claude Code cannot use the answer schema {request.schema.__name__}: {detail}"
                ) from error
            raise call.unexplained(f"Claude Code stopped unexpectedly: {detail}") from error
        result = call.result
        if result is None:
            raise RetryableCallError("Claude Code finished without a result")
        if result.is_error:
            raise call.failure(result)
        return AgentResult(
            output=_validate(request.schema, result.structured_output, _cost(result)),
            session_id=result.session_id,
            cost=_cost(result),
        )


class _LostSessionError(Exception):
    """The session to resume is gone. Not a failure of the call: it starts afresh."""


class _Call:
    """What one session reported, and how to explain it when it fails."""

    def __init__(self, spend_cap_usd: float, *, by_request: bool, resumed: bool) -> None:
        self.spend_cap_usd = spend_cap_usd
        self.by_request = by_request
        """Whether the cap is the request's, rather than the per-call one."""
        self.resumed = resumed
        self.result: ResultMessage | None = None
        self.trouble: str | None = None
        """The error Claude Code reported on its last failed API call, if any."""
        self.limit_resets_at: int | None = None
        """When a usage limit that refused the session lifts."""

    def see(self, message: Message) -> None:
        if isinstance(message, ResultMessage):
            self.result = message
        elif isinstance(message, AssistantMessage) and message.error is not None:
            self.trouble = message.error
        elif isinstance(message, RateLimitEvent):
            info = message.rate_limit_info
            if info.status == "rejected":
                self.limit_resets_at = info.resets_at

    def failure(self, result: ResultMessage) -> CallError | _LostSessionError:
        cost = _cost(result)
        errors = [error.strip() for error in result.errors or () if error.strip()]
        if self.resumed and any(LOST_SESSION in error for error in errors):
            return _LostSessionError()
        detail = "; ".join(errors) or (result.result or "").strip() or result.subtype
        if result.subtype == "error_max_budget_usd":
            source = "" if self.by_request else " (budget.per_call in the charter)"
            return SpendCapReached(
                f"the call reached its spend cap of ${self.spend_cap_usd:.2f}{source} "
                f"after spending ${cost.usd:.2f}",
                cost=cost,
                by_request=self.by_request,
            )
        if result.subtype == "error_max_structured_output_retries":
            return RetryableCallError(
                f"Claude Code could not produce an answer in the required shape: {detail}",
                cost=cost,
            )
        return self._by_cause(result.api_error_status, detail, cost)

    def unexplained(self, message: str) -> CallError:
        """A session that broke off without a result to explain it."""
        cost = _cost(self.result) if self.result else Cost()
        return self._by_cause(None, message, cost)

    def _by_cause(self, status: int | None, detail: str, cost: Cost) -> CallError:
        if self.trouble == "authentication_failed" or status in (401, 403):
            return FatalCallError(f"{SIGN_IN} ({detail})", cost=cost)
        if self.trouble == "billing_error" or status == 402:
            return FatalCallError(
                f"the Anthropic account has a billing problem: {detail}", cost=cost
            )
        if self.trouble == "rate_limit" or status == 429:
            if self.limit_resets_at is not None:
                resets = datetime.fromtimestamp(self.limit_resets_at).strftime("%Y-%m-%d %H:%M")
                return FatalCallError(
                    f"the Claude usage limit is reached until {resets}; resume the run then "
                    f"({detail})",
                    cost=cost,
                )
            return RetryableCallError(f"rate limited: {detail}", cost=cost)
        if self.trouble == "invalid_request" or (
            status is not None and 400 <= status < 500 and status not in _RETRYABLE_STATUSES
        ):
            return FatalCallError(
                f"the request was refused, so retrying will not help: {detail}", cost=cost
            )
        return RetryableCallError(f"Claude Code failed: {detail}", cost=cost)


def answer_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema Claude Code is given for the answer."""
    return cast("dict[str, Any]", _without_annotations(schema.model_json_schema()))


def _without_annotations(node: object, *, names: bool = False) -> object:
    """`node` without the annotation keywords. In a mapping of names, such as
    `properties`, the keys are names rather than keywords, so all are kept."""
    if isinstance(node, list):
        return [_without_annotations(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _without_annotations(value, names=not names and key in _NAMED)
        for key, value in node.items()
        if names or key not in _ANNOTATIONS
    }


_NAMED = frozenset({"properties", "$defs", "definitions", "patternProperties"})


def read_only_hook(repo: Path) -> HookCallback:
    """A PreToolUse hook that lets only the read-only tools run, and only inside `repo`."""
    root = repo.resolve()

    async def hook(
        input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if input["hook_event_name"] != "PreToolUse":
            return {}
        problem = refusal(root, input["tool_name"], input["tool_input"])
        if problem is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": problem,
            }
        }

    return hook


def refusal(root: Path, tool: str, tool_input: dict[str, Any]) -> str | None:
    """Why this tool call may not run, or None if it may."""
    if tool == ANSWER_TOOL:
        return None
    if tool not in READ_ONLY_TOOLS:
        return f"{tool} is not available: this review may only read and search the repository."
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not _inside(root, value):
            return f"{value!r} is outside the repository; only files inside {root} can be read."
    pattern = tool_input.get("pattern")
    if tool == "Glob" and isinstance(pattern, str) and _escapes(pattern):
        return f"the pattern {pattern!r} reaches outside the repository."
    return None


def _inside(root: Path, value: str) -> bool:
    path = Path(value).expanduser()
    target = path if path.is_absolute() else root / path
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or resolved.is_relative_to(root)


def _escapes(pattern: str) -> bool:
    path = PurePath(pattern)
    return path.is_absolute() or pattern.startswith("~") or ".." in path.parts


def _validate(schema: type[OutputT], output: object, cost: Cost) -> OutputT:
    if output is None:
        raise RetryableCallError(
            f"Claude Code answered without the structured output {schema.__name__}", cost=cost
        )
    try:
        return schema.model_validate(output)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'answer'}: {issue['msg']}"
            for issue in error.errors()[:5]
        )
        raise RetryableCallError(
            f"the answer does not fit {schema.__name__} ({problems})", cost=cost
        ) from error


def _cost(result: ResultMessage) -> Cost:
    """What the session spent. Input tokens include those written to and read from
    the prompt cache, since all of them were processed."""
    usage = result.usage or {}

    def tokens(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and value > 0 else 0

    return Cost(
        usd=max(result.total_cost_usd or 0.0, 0.0),
        input_tokens=tokens("input_tokens")
        + tokens("cache_creation_input_tokens")
        + tokens("cache_read_input_tokens"),
        output_tokens=tokens("output_tokens"),
    )


def _result_from(error: ResultError) -> ResultMessage:
    """The failed result an exception carries, when the stream did not deliver it."""
    data = error.data
    cost = data.get("total_cost_usd")
    usage = data.get("usage")
    result = ResultMessage(
        subtype=error.subtype or "error_during_execution",
        duration_ms=0,
        duration_api_ms=0,
        is_error=True,
        num_turns=0,
        session_id=error.session_id or "",
        errors=error.errors,
        result=error.result,
        api_error_status=error.api_error_status,
    )
    with contextlib.suppress(TypeError, ValueError):
        result.total_cost_usd = float(cost) if cost is not None else None
    result.usage = usage if isinstance(usage, dict) else None
    return result
