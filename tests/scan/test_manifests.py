import json
from pathlib import Path, PurePosixPath

from aiarb.scan.files import RepoFiles
from aiarb.scan.manifests import Manifest, manifests
from tests.scan.repos import make


def read(tmp_path: Path, files: dict[str, str]) -> list[Manifest]:
    return manifests(RepoFiles.scan(make(tmp_path, files)))


def test_package_json(tmp_path: Path) -> None:
    package = {
        "engines": {"node": ">=20"},
        "dependencies": {"next": "15.0.0", "react": "^19"},
        "devDependencies": {"vitest": "^2"},
    }

    [manifest] = read(tmp_path, {"web/package.json": json.dumps(package)})

    assert manifest == Manifest(
        PurePosixPath("web/package.json"), "npm", ">=20", ("next", "react"), ("vitest",)
    )


def test_pyproject_with_project_tables_and_dependency_groups(tmp_path: Path) -> None:
    pyproject = """
[project]
requires-python = ">=3.11"
dependencies = [
    "FastAPI[all]>=0.110",
    "SQLAlchemy~=2.0; python_version<'3.13'",
    "pydantic_settings",
]
[project.optional-dependencies]
redis = ["redis>=5"]
[dependency-groups]
dev = ["pytest>=8", {include-group = "lint"}]
lint = ["ruff"]
"""
    [manifest] = read(tmp_path, {"pyproject.toml": pyproject})

    assert manifest.runtime == ">=3.11"
    assert manifest.dependencies == ("fastapi", "sqlalchemy", "pydantic-settings", "redis")
    assert manifest.dev_dependencies == ("pytest", "ruff")


def test_pyproject_with_poetry(tmp_path: Path) -> None:
    pyproject = """
[tool.poetry.dependencies]
python = "^3.12"
django = "^5.0"
[tool.poetry.group.test.dependencies]
pytest-django = "*"
"""
    [manifest] = read(tmp_path, {"pyproject.toml": pyproject})

    assert (manifest.runtime, manifest.dependencies, manifest.dev_dependencies) == (
        "^3.12",
        ("django",),
        ("pytest-django",),
    )


def test_a_pyproject_for_tools_only_is_not_a_manifest(tmp_path: Path) -> None:
    assert read(tmp_path, {"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}) == []


def test_requirements_files_name_their_kind(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "requirements.txt": "# app\nflask==3.0\n-r base.txt\ngunicorn  # server\n",
            "requirements-dev.txt": "pytest\n",
        },
    )

    assert [(m.dependencies, m.dev_dependencies) for m in found] == [
        ((), ("pytest",)),
        (("flask", "gunicorn"), ()),
    ]


def test_go_mod_lists_direct_requirements_only(tmp_path: Path) -> None:
    go_mod = """module example.com/app

go 1.22

require github.com/gin-gonic/gin v1.10.0

require (
\tgithub.com/redis/go-redis/v9 v9.5.1
\tgolang.org/x/sys v0.20.0 // indirect
)
"""
    [manifest] = read(tmp_path, {"go.mod": go_mod})

    assert manifest.runtime == "1.22"
    assert manifest.dependencies == ("github.com/gin-gonic/gin", "github.com/redis/go-redis/v9")


def test_cargo_gemfile_and_composer(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "Cargo.toml": '[package]\nrust-version = "1.80"\n[dependencies]\naxum = "0.7"\n'
            '[dev-dependencies]\ninsta = "1"\n',
            "Gemfile": 'ruby "3.3.0"\ngem "rails", "~> 7.1"\ngroup :development, :test do\n'
            '  gem "rspec-rails"\nend\ngem "pg"\n',
            "composer.json": json.dumps(
                {
                    "require": {"php": "^8.2", "ext-json": "*", "laravel/framework": "^11"},
                    "require-dev": {"phpunit/phpunit": "^11"},
                }
            ),
        },
    )

    assert [(m.ecosystem, m.runtime, m.dependencies, m.dev_dependencies) for m in found] == [
        ("Rust", "1.80", ("axum",), ("insta",)),
        ("Ruby", "3.3.0", ("rails", "pg"), ("rspec-rails",)),
        ("PHP", "^8.2", ("laravel/framework",), ("phpunit/phpunit",)),
    ]


def test_a_broken_manifest_is_skipped(tmp_path: Path) -> None:
    assert read(tmp_path, {"package.json": "{not json", "Cargo.toml": "[[["}) == []
