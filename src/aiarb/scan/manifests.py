"""Dependency manifests, read into one shape whatever the ecosystem.

Only names are kept, not version ranges: the agents need to know what the app is
built on, and can read the manifest itself when a version matters.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from aiarb.scan.files import RepoFiles


@dataclass(frozen=True)
class Manifest:
    path: PurePosixPath
    ecosystem: str
    runtime: str | None
    """The language version it asks for, such as `>=3.11` or `1.22`."""
    dependencies: tuple[str, ...]
    dev_dependencies: tuple[str, ...]

    @property
    def all_dependencies(self) -> frozenset[str]:
        return frozenset(self.dependencies) | frozenset(self.dev_dependencies)


LOCKFILES = {
    "package-lock.json": "npm",
    "yarn.lock": "Yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "Bun",
    "bun.lock": "Bun",
    "uv.lock": "uv",
    "poetry.lock": "Poetry",
    "Pipfile.lock": "Pipenv",
    "pdm.lock": "PDM",
    "Cargo.lock": "Cargo",
    "go.sum": "Go modules",
    "Gemfile.lock": "Bundler",
    "composer.lock": "Composer",
}


def manifests(files: RepoFiles) -> list[Manifest]:
    """Every manifest in the repo that could be read, in path order."""
    found: list[Manifest] = []
    for path in files.paths:
        parse = _PARSERS.get(path.name) or (_requirements if _is_requirements(path.name) else None)
        text = files.read(path) if parse else None
        if parse is None or text is None:
            continue
        try:
            manifest = parse(path, text)
        except (ValueError, TypeError, AttributeError):
            continue
        if manifest is not None:
            found.append(manifest)
    return found


def _package_json(path: PurePosixPath, text: str) -> Manifest:
    data = _mapping(json.loads(text))
    engines = _mapping(data.get("engines"))
    return Manifest(
        path,
        "npm",
        _text(engines.get("node")),
        _names(_mapping(data.get("dependencies"))),
        _names(_mapping(data.get("devDependencies"))),
    )


def _pyproject(path: PurePosixPath, text: str) -> Manifest | None:
    data = tomllib.loads(text)
    project = _mapping(data.get("project"))
    poetry = _mapping(_mapping(data.get("tool")).get("poetry"))
    if not (project or poetry):
        return None
    runtime = _text(project.get("requires-python")) or _text(
        _mapping(poetry.get("dependencies")).get("python")
    )
    dependencies = [*_strings(project.get("dependencies"))]
    for extra in _mapping(project.get("optional-dependencies")).values():
        dependencies += _strings(extra)
    dependencies += [
        name for name in _mapping(poetry.get("dependencies")) if name.lower() != "python"
    ]
    dev: list[str] = []
    for group in _mapping(data.get("dependency-groups")).values():
        dev += _strings(group)
    for group in _mapping(poetry.get("group")).values():
        dev += list(_mapping(_mapping(group).get("dependencies")))
    dev += list(_mapping(poetry.get("dev-dependencies")))
    return Manifest(path, "Python", runtime, _python_names(dependencies), _python_names(dev))


def _requirements(path: PurePosixPath, text: str) -> Manifest:
    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    names = _python_names(line for line in lines if line and not line.startswith("-"))
    is_dev = any(word in path.name for word in ("dev", "test", "lint", "doc"))
    return Manifest(path, "Python", None, () if is_dev else names, names if is_dev else ())


def _go_mod(path: PurePosixPath, text: str) -> Manifest:
    runtime = re.search(r"^go\s+(\S+)", text, re.MULTILINE)
    required: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require ") and "(" not in line:
            line = line.removeprefix("require ").strip()
        elif not in_block:
            continue
        if line and not line.startswith("//") and "// indirect" not in line:
            required.append(line.split()[0])
    return Manifest(path, "Go", runtime.group(1) if runtime else None, tuple(required), ())


def _cargo_toml(path: PurePosixPath, text: str) -> Manifest:
    data = tomllib.loads(text)
    package = _mapping(data.get("package"))
    return Manifest(
        path,
        "Rust",
        _text(package.get("rust-version")),
        _names(_mapping(data.get("dependencies"))),
        _names(_mapping(data.get("dev-dependencies"))),
    )


def _gemfile(path: PurePosixPath, text: str) -> Manifest:
    runtime = re.search(r"""^\s*ruby\s+["']([^"']+)""", text, re.MULTILINE)
    dependencies: list[str] = []
    dev: list[str] = []
    dev_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("group ") and stripped.endswith(" do"):
            dev_block = ":development" in stripped or ":test" in stripped
        elif stripped == "end":
            dev_block = False
        elif gem := re.match(r"""gem\s+["']([^"']+)""", stripped):
            (dev if dev_block else dependencies).append(gem.group(1))
    return Manifest(
        path, "Ruby", runtime.group(1) if runtime else None, tuple(dependencies), tuple(dev)
    )


def _composer_json(path: PurePosixPath, text: str) -> Manifest:
    data = _mapping(json.loads(text))
    require = _mapping(data.get("require"))

    def packages(section: dict[str, Any]) -> tuple[str, ...]:
        return tuple(name for name in section if name != "php" and not name.startswith("ext-"))

    return Manifest(
        path,
        "PHP",
        _text(require.get("php")),
        packages(require),
        packages(_mapping(data.get("require-dev"))),
    )


_PARSERS: dict[str, Callable[[PurePosixPath, str], Manifest | None]] = {
    "package.json": _package_json,
    "pyproject.toml": _pyproject,
    "go.mod": _go_mod,
    "Cargo.toml": _cargo_toml,
    "Gemfile": _gemfile,
    "composer.json": _composer_json,
}


def _is_requirements(name: str) -> bool:
    return name.startswith("requirements") and name.endswith(".txt")


def _python_names(requirements: Iterable[str]) -> tuple[str, ...]:
    """`FastAPI[all]>=0.110; python_version<'3.13'` → `fastapi`."""
    names: list[str] = []
    for requirement in requirements:
        found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        if found:
            name = re.sub(r"[-_.]+", "-", found.group(1)).lower()
            if name not in names:
                names.append(name)
    return tuple(names)


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _names(section: dict[str, Any]) -> tuple[str, ...]:
    return tuple(section)


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None
