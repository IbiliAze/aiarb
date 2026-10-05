"""What runs on every push: the CI pipelines and the commands they run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from aiarb.scan.section import Section

if TYPE_CHECKING:
    from pathlib import PurePosixPath

    from aiarb.scan.files import RepoFiles

OTHER_CI = {
    ".circleci/config.yml": "CircleCI",
    "Jenkinsfile": "Jenkins",
    "azure-pipelines.yml": "Azure Pipelines",
    "bitbucket-pipelines.yml": "Bitbucket Pipelines",
    ".travis.yml": "Travis CI",
    ".drone.yml": "Drone",
    ".buildkite/pipeline.yml": "Buildkite",
}
GITLAB_KEYWORDS = frozenset(
    {
        "stages",
        "variables",
        "default",
        "include",
        "workflow",
        "image",
        "services",
        "before_script",
        "after_script",
        "cache",
    }
)
COMMANDS_PER_JOB = 6
COMMAND_LENGTH = 100


def collect(files: RepoFiles) -> Section | None:
    facts: list[str] = []
    for path in files.matching(".github/workflows/*.yml", ".github/workflows/*.yaml"):
        facts += _github(path, _load(files, path))
    if files.has(".gitlab-ci.yml"):
        facts += _gitlab(_load(files, ".gitlab-ci.yml"))
    facts += [f"{name}: {path}" for path, name in OTHER_CI.items() if files.has(path)]
    return Section("CI", tuple(facts)) if facts else None


def _github(path: PurePosixPath, workflow: dict[Any, Any]) -> list[str]:
    # YAML 1.1 reads a bare `on:` key as True.
    triggers = workflow.get("on", workflow.get(True))
    if isinstance(triggers, dict):
        triggers = list(triggers)
    elif isinstance(triggers, str):
        triggers = [triggers]
    name = workflow.get("name") or path.stem
    facts = [
        f"GitHub Actions, {path}: {name!s}"
        + (f", on {', '.join(map(str, triggers))}" if isinstance(triggers, list) else "")
    ]
    for job_id, job in _mapping(workflow.get("jobs")).items():
        steps = _mapping(job).get("steps")
        commands = (
            [_mapping(step).get("run") for step in steps if isinstance(step, dict)]
            if isinstance(steps, list)
            else []
        )
        facts.append(f"{path} job {job_id}: {_commands(commands)}")
    return facts


def _gitlab(pipeline: dict[Any, Any]) -> list[str]:
    stages = pipeline.get("stages")
    facts = [
        "GitLab CI, .gitlab-ci.yml"
        + (f": stages {', '.join(map(str, stages))}" if isinstance(stages, list) else "")
    ]
    for job_id, job in pipeline.items():
        if job_id in GITLAB_KEYWORDS or str(job_id).startswith(".") or not isinstance(job, dict):
            continue
        script = job.get("script")
        commands = script if isinstance(script, list) else [script]
        facts.append(f".gitlab-ci.yml job {job_id}: {_commands(commands)}")
    return facts


def _commands(commands: list[Any]) -> str:
    lines: list[str] = []
    for command in commands:
        if isinstance(command, str) and command.strip():
            first = command.strip().splitlines()[0]
            lines.append(
                first if len(first) <= COMMAND_LENGTH else first[: COMMAND_LENGTH - 1] + "…"
            )
    if not lines:
        return "no shell commands"
    shown = lines[:COMMANDS_PER_JOB]
    more = f" (+{len(lines) - len(shown)} more)" if len(lines) > len(shown) else ""
    return "; ".join(f"`{line}`" for line in shown) + more


def _load(files: RepoFiles, path: PurePosixPath | str) -> dict[Any, Any]:
    try:
        return _mapping(yaml.safe_load(files.read(path) or ""))
    except yaml.YAMLError:
        return {}


def _mapping(value: object) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}
