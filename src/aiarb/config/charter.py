"""The charter: how the board behaves in one repo, from `.aiarb/charter.yaml`.

Every field has a default, so an empty charter is a valid one. Unknown keys are
refused rather than ignored, so a typo fails loudly instead of silently falling
back to a default.
"""

import dataclasses
import difflib
from collections.abc import Hashable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import ErrorDetails

from aiarb.domain.scoring import SeverityThresholds

CHARTER_PATH = Path(".aiarb/charter.yaml")
"""Where the charter lives, relative to the root of the target repo."""

ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PriorityName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
Constraint = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Models(_Section):
    """The model each role runs on. Any model ID Claude Code accepts."""

    planner: ModelId = "claude-opus-5-5"
    critic: ModelId = "claude-sonnet-5-5"
    judge: ModelId = "claude-opus-5-5"
    verifier: ModelId = "claude-sonnet-5-5"
    explainer: ModelId = "claude-sonnet-5-5"
    scorer: ModelId = "claude-sonnet-5-5"
    """Used by the language-model scorer, whether chosen or as Jev's fallback."""

    @model_validator(mode="after")
    def _critic_differs_from_planner(self) -> "Models":
        if self.critic == self.planner:
            raise ValueError(
                f"the critic must be a different model from the planner (both are "
                f"{self.planner!r}): a model reviewing its own plan shares its blind spots"
            )
        return self


class Debate(_Section):
    max_rounds: int = Field(default=4, ge=1)


class Scoring(_Section):
    provider: Literal["jev", "llm"] = "llm"
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    """Below this confidence, a language model confirms the score."""
    thresholds: SeverityThresholds = SeverityThresholds()


class Budget(_Section):
    """Spend caps in US dollars."""

    per_call: float = Field(default=10, gt=0)
    per_run: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _call_fits_in_run(self) -> "Budget":
        if self.per_call > self.per_run:
            raise ValueError(
                f"the per-call cap ({self.per_call}) cannot exceed the per-run cap ({self.per_run})"
            )
        return self


class Charter(_Section):
    models: Models = Models()
    debate: Debate = Debate()
    scoring: Scoring = Scoring()
    priorities: dict[PriorityName, Annotated[int, Field(ge=1, le=5)]] = Field(default_factory=dict)
    """What matters in this codebase, weighted from 1 (a little) to 5 (most)."""
    constraints: tuple[Constraint, ...] = ()
    """Rules every plan must respect, such as "no new managed services"."""
    budget: Budget = Budget()

    @model_validator(mode="before")
    @classmethod
    def _empty_sections_use_defaults(cls, data: Any) -> Any:
        # A section whose settings are all commented out loads from YAML as null.
        if isinstance(data, dict):
            sections = cast("dict[str, Any]", data)
            return {key: value for key, value in sections.items() if value is not None}
        return data


class CharterError(Exception):
    """The charter file exists but cannot be used."""

    def __init__(self, path: Path, summary: str, problems: Sequence[str] = ()) -> None:
        self.path = path
        self.summary = summary
        self.problems = tuple(problems)
        super().__init__("\n".join([f"{path} {summary}", *(f"  {p}" for p in self.problems)]))


def load_charter(repo: Path) -> Charter:
    """Load the repo's charter, or the defaults if it has none."""
    path = repo / CHARTER_PATH
    if not path.exists():
        return Charter()
    try:
        data, root = _parse(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CharterError(path, f"cannot be read: {error.strerror or error}") from error
    except UnicodeDecodeError as error:
        raise CharterError(path, "is not UTF-8 text") from error
    except yaml.MarkedYAMLError as error:
        raise CharterError(path, "is not valid YAML:", [_yaml_problem(error)]) from error
    except yaml.YAMLError as error:
        raise CharterError(path, "is not valid YAML:", [str(error)]) from error
    if data is None:
        return Charter()
    if not isinstance(data, dict):
        raise CharterError(
            path, f"must be sections such as `models:` and `debate:`, not {_describe(data)}"
        )
    try:
        return Charter.model_validate(data)
    except ValidationError as error:
        problems = sorted(
            (_problem(detail, root) for detail in error.errors()),
            key=lambda problem: (problem[0] is None, problem[0] or 0),
        )
        count = len(problems)
        raise CharterError(
            path,
            f"has {count} problem{'s' if count > 1 else ''}:",
            [text if line is None else f"line {line}, {text}" for line, text in problems],
        ) from error


class _StrictLoader(yaml.SafeLoader):
    """A safe loader that refuses duplicate keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        first_seen: dict[str, int] = {}
        for key_node, _ in node.value:
            # Keys are compared as written. Charter keys are plain names, so
            # there is no need to construct them first.
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            key = str(key_node.value)
            if key in first_seen:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"duplicate key {key!r}, first defined on line {first_seen[key]}",
                    key_node.start_mark,
                )
            first_seen[key] = key_node.start_mark.line + 1
        return super().construct_mapping(node, deep=deep)


def _parse(text: str) -> tuple[Any, yaml.Node | None]:
    """The charter's data, and its YAML nodes for finding line numbers."""
    data = yaml.load(text, Loader=_StrictLoader)
    loader = _StrictLoader(text)
    try:
        return data, loader.get_single_node()
    finally:
        loader.dispose()


def _yaml_problem(error: yaml.MarkedYAMLError) -> str:
    problem = error.problem or str(error)
    mark = error.problem_mark
    if mark is None:
        return problem
    return f"line {mark.line + 1}, column {mark.column + 1}: {problem}"


def _problem(detail: ErrorDetails, root: yaml.Node | None) -> tuple[int | None, str]:
    """One validation error as (line, "setting: message")."""
    loc = detail["loc"]
    if loc and loc[-1] == "[key]":
        # The name of an entry is wrong, not its value: point at the name.
        return _line_of(root, loc[:-1]), f"{_where(loc[:-2])}: name {loc[-2]!r} {_message(detail)}"
    message = _message(detail)
    value = detail["input"]
    if detail["type"] not in _NO_GOT and isinstance(value, str | int | float):
        message += f" (got {value!r})"
    return _line_of(root, loc), f"{_where(loc)}: {message}"


_UNKNOWN_SETTING = frozenset({"extra_forbidden", "unexpected_keyword_argument"})
_NOT_A_SECTION = frozenset({"model_type", "dataclass_type", "dict_type"})
_NO_GOT = _UNKNOWN_SETTING | _NOT_A_SECTION | {"value_error"}
_SNAKE_CASE = "must be lowercase words joined by underscores, like data_integrity"


def _message(detail: ErrorDetails) -> str:
    kind, ctx = detail["type"], detail.get("ctx", {})
    if kind in _UNKNOWN_SETTING:
        return _unknown_setting(detail["loc"])
    if kind in _NOT_A_SECTION:
        found = _describe(detail["input"])
        return f"must be a section with settings indented below it, not {found}"
    match kind:
        case "value_error":
            return str(ctx.get("error", detail["msg"]))
        case "string_too_short":
            return "must not be blank"
        case "string_pattern_mismatch":
            return _SNAKE_CASE
        case "string_type":
            return "must be text"
        case "greater_than_equal":
            return f"must be at least {_number(ctx['ge'])}"
        case "greater_than":
            return f"must be more than {_number(ctx['gt'])}"
        case "less_than_equal":
            return f"must be at most {_number(ctx['le'])}"
        case "less_than":
            return f"must be less than {_number(ctx['lt'])}"
        case "literal_error":
            return f"must be {ctx['expected']}"
        case "int_parsing" | "int_type" | "int_from_float":
            return "must be a whole number"
        case "float_parsing" | "float_type":
            return "must be a number"
        case "list_type" | "tuple_type":
            return "must be a list"
        case _:
            return detail["msg"].replace("Input should be", "must be", 1)


def _unknown_setting(loc: tuple[int | str, ...]) -> str:
    known = _settings_under(loc[:-1])
    if not known:
        return "unknown setting"
    match = difflib.get_close_matches(str(loc[-1]), known, n=1)
    if match:
        return f"unknown setting; did you mean {match[0]!r}?"
    return f"unknown setting; expected one of {', '.join(known)}"


def _settings_under(loc: tuple[int | str, ...]) -> list[str]:
    """The names a section accepts, found by walking the charter's types."""
    section: Any = Charter
    for part in loc:
        section = _fields(section).get(str(part))
    return list(_fields(section))


def _fields(section: Any) -> dict[str, Any]:
    if isinstance(section, type) and issubclass(section, BaseModel):
        return {name: field.annotation for name, field in section.model_fields.items()}
    if isinstance(section, type) and dataclasses.is_dataclass(section):
        return {field.name: field.type for field in dataclasses.fields(section)}
    return {}


def _where(loc: tuple[int | str, ...]) -> str:
    where = ""
    for part in loc:
        where += f"[{part}]" if isinstance(part, int) else f".{part}"
    return where.removeprefix(".") or "charter"


def _line_of(root: yaml.Node | None, loc: tuple[int | str, ...]) -> int | None:
    """The line of the deepest part of `loc` present in the file."""
    node, line = root, None
    for part in loc:
        if isinstance(node, yaml.MappingNode):
            entry = next(
                ((key, value) for key, value in node.value if key.value == str(part)), None
            )
            if entry is None:
                break
            line, node = entry[0].start_mark.line + 1, entry[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            if part >= len(node.value):
                break
            node = node.value[part]
            line = node.start_mark.line + 1
        else:
            break
    return line


def _number(value: Any) -> Any:
    """1 rather than 1.0, for bounds on float settings."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _describe(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return f"{value!r}".lower()
    if isinstance(value, int | float):
        return f"the number {value}"
    if isinstance(value, str):
        return f"the text {value!r}"
    if isinstance(value, list):
        return "a list"
    return f"a {type(value).__name__}"
