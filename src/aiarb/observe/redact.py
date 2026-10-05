"""Taking secrets out of what a run writes and prints.

The agents read the repo, and can quote what they find there, a hard-coded key
included, into objections and the plan. Everything a run writes for people to
read or share goes through a `Redactor` first: the output files, the run's log,
and what the commands print.

Redaction is best effort. It catches the values of secrets in aiarb's own
environment, keys in well-known formats, private key blocks, passwords in URLs,
and values assigned to names like `password` or `api_key`. The checkpoints keep
the run's text as it was, since a resumed run must carry on from what the agents
actually said; they live in `.aiarb/runs/`, which is kept out of git.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

REDACTED = "[redacted]"

_SECRET_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_?KEY|SECRET(?:_?KEY)?|TOKEN|PASSWORD|PASSWD|PRIVATE_?KEY"
    r"|CREDENTIALS?|KEY)(?:$|_)",
    re.IGNORECASE,
)
"""Environment variables whose values are taken for secrets."""

_MIN_SECRET_LENGTH = 12
"""Shorter environment values are left alone: too likely to be ordinary words."""

_FORMATS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
        r"\bsk-ant-[A-Za-z0-9_-]{20,}",  # Anthropic
        r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",  # OpenAI
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",  # AWS access key ID
        r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",  # GitHub
        r"\bgithub_pat_[A-Za-z0-9_]{40,}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",  # GitLab
        r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b",  # Slack
        r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b",  # Stripe
        r"\bAIza[0-9A-Za-z_-]{35}\b",  # Google
        r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b",  # SendGrid
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWT
    )
)
"""Secrets recognisable by their shape alone."""

_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]{16,}=*")
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)", re.IGNORECASE)
_ASSIGNED = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[\w-]*
     ["']?\s*(?:=>|[:=])\s*)
    (["']?)
    ([^\s"'`,;)]{8,})
    \2
    """
)
"""A value assigned to a secret-sounding name, as in a config file or code."""


@dataclass(frozen=True)
class Redactor:
    secrets: tuple[str, ...] = ()
    """Exact values to take out wherever they appear, longest first."""

    def __post_init__(self) -> None:
        values = {value for value in self.secrets if value}
        object.__setattr__(self, "secrets", tuple(sorted(values, key=len, reverse=True)))

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Redactor:
        """A redactor that also knows the secrets in the environment, such as
        ANTHROPIC_API_KEY."""
        environ = os.environ if environ is None else environ
        return cls(
            tuple(
                value
                for name, value in environ.items()
                if _SECRET_NAME.search(name) and _looks_secret(value)
            )
        )

    def text(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _FORMATS:
            text = pattern.sub(REDACTED, text)
        text = _BEARER.sub(rf"\1{REDACTED}", text)
        text = _URL_PASSWORD.sub(rf"\1{REDACTED}\2", text)
        return _ASSIGNED.sub(_assigned, text)

    def data(self, value: Any) -> Any:
        """`value` with every string in it redacted, through dicts, lists and tuples.
        Keys are left alone."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
        if isinstance(value, list | tuple):
            return [self.data(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
        return value


def _looks_secret(value: str) -> bool:
    """Long, without spaces, and mixing letters and digits, as keys and tokens do.
    Paths and ordinary settings are left alone."""
    return (
        len(value) >= _MIN_SECRET_LENGTH
        and not any(char.isspace() for char in value)
        and not value.startswith(("/", "~", "."))
        and any(char.isdigit() for char in value)
        and any(char.isalpha() for char in value)
    )


def _assigned(match: re.Match[str]) -> str:
    """The value redacted if it looks like a secret rather than code: quoted, or
    mixing letters and digits. `password = request.form` is left alone."""
    name, quote, value = match.group(1), match.group(2), match.group(3)
    if value == REDACTED or not (quote or _mixed(value)) or _code(value):
        return match.group(0)
    return f"{name}{quote}{REDACTED}{quote}"


def _mixed(value: str) -> bool:
    return any(char.isdigit() for char in value) and any(char.isalpha() for char in value)


def _code(value: str) -> bool:
    """An expression rather than a literal: a call, attribute or lookup."""
    return any(char in value for char in "()[]{}<>") or bool(
        re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", value)
    )
