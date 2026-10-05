import json
from pathlib import Path

from aiarb.scan.collectors import COLLECTORS, churn, ci, dependencies, infrastructure, stack, tests
from aiarb.scan.files import RepoFiles
from aiarb.scan.section import Section
from tests.scan.repos import commit, init, make


def scan(tmp_path: Path, files: dict[str, str]) -> RepoFiles:
    return RepoFiles.scan(make(tmp_path, files))


def facts(section: Section | None) -> list[str]:
    assert section is not None
    return list(section.facts)


class TestStack:
    def test_languages_frameworks_and_runtimes(self, tmp_path: Path) -> None:
        files = scan(
            tmp_path,
            {
                "api/main.py": "",
                "api/models.py": "",
                "web/app.tsx": "",
                "web/package.json": json.dumps({"dependencies": {"next": "15", "react": "19"}}),
                "pyproject.toml": '[project]\nrequires-python = ">=3.12"\n'
                'dependencies = ["fastapi"]\n',
                ".nvmrc": "20.11\n",
                "README.md": "",
            },
        )

        assert facts(stack.collect(files)) == [
            "Languages: Python (2 files), TypeScript (1 file)",
            "Frameworks: FastAPI, Next.js, React",
            "Runtime versions: Python >=3.12 (pyproject.toml), 20.11 (.nvmrc)",
        ]

    def test_nothing_to_say_about_an_empty_repo(self, tmp_path: Path) -> None:
        assert stack.collect(scan(tmp_path, {"notes.md": ""})) is None


class TestDependencies:
    def test_each_manifest_and_the_lockfiles(self, tmp_path: Path) -> None:
        files = scan(
            tmp_path,
            {
                "pyproject.toml": '[project]\ndependencies = ["httpx"]\n',
                "uv.lock": "",
            },
        )

        assert facts(dependencies.collect(files)) == [
            "pyproject.toml (Python): runtime: httpx; dev: none",
            "Lockfiles: uv.lock (uv)",
        ]

    def test_long_lists_are_cut_short(self, tmp_path: Path) -> None:
        names = [f"lib{n}" for n in range(30)]
        files = scan(tmp_path, {"requirements.txt": "\n".join(names)})

        [listing, locks] = facts(dependencies.collect(files))

        assert listing.endswith("lib24 (+5 more); dev: none")
        assert locks == "Lockfiles: none"

    def test_no_manifests(self, tmp_path: Path) -> None:
        assert dependencies.collect(scan(tmp_path, {"main.c": ""})) is None


class TestTests:
    def test_counts_folders_and_tools(self, tmp_path: Path) -> None:
        files = scan(
            tmp_path,
            {
                "src/queue.py": "",
                "src/api.py": "",
                "src/queue_test.py": "",
                "tests/test_api.py": "",
                "tests/conftest.py": "",
                "web/button.test.tsx": "",
                "web/button.tsx": "",
                "web/package.json": json.dumps({"devDependencies": {"@playwright/test": "1"}}),
            },
        )

        assert facts(tests.collect(files)) == [
            "Test files: 4, against 3 source files.",
            "Where they are: tests/ (2), src/ (1), web/ (1)",
            "Test tools: Playwright, pytest",
        ]

    def test_go_tests_run_with_go_test(self, tmp_path: Path) -> None:
        files = scan(tmp_path, {"queue.go": "", "queue_test.go": ""})

        assert "Test tools: go test" in facts(tests.collect(files))

    def test_no_tests_is_itself_a_fact(self, tmp_path: Path) -> None:
        assert facts(tests.collect(scan(tmp_path, {"app.rb": ""}))) == [
            "No test files found, against 1 source file."
        ]


class TestInfrastructure:
    def test_containers_deploy_config_and_schema_changes(self, tmp_path: Path) -> None:
        files = scan(
            tmp_path,
            {
                "Dockerfile": "FROM --platform=linux/amd64 python:3.12-slim AS build\n"
                "FROM gcr.io/distroless/python3\n",
                "worker/Dockerfile": "ARG BASE\nFROM ${BASE}\nFROM scratch\n",
                "docker-compose.yml": "",
                "deploy/k8s/web.yaml": "",
                "infra/main.tf": "",
                "infra/vars.tf": "",
                "fly.toml": "",
                "alembic/versions/001_init.py": "",
                "alembic/versions/002_queue.py": "",
            },
        )

        assert facts(infrastructure.collect(files)) == [
            "Containers: Dockerfile, docker-compose.yml, worker/Dockerfile",
            "Base images: gcr.io/distroless/python3, python:3.12-slim",
            "Kubernetes: deploy/k8s/web.yaml",
            "Terraform: infra/ (2 files)",
            "Hosting and deploy config: fly.toml",
            "Migrations: alembic/ (2 files)",
        ]

    def test_environment_variable_names_but_never_values(self, tmp_path: Path) -> None:
        files = scan(
            tmp_path,
            {
                ".env.example": "# Redis\nREDIS_URL=redis://localhost\nexport API_KEY=changeme\n",
                ".env": "SECRET_TOKEN=do-not-read\n",
            },
        )

        found = facts(infrastructure.collect(files))

        assert found == ["Environment variables (.env.example): REDIS_URL, API_KEY"]
        assert not any("SECRET_TOKEN" in fact or "localhost" in fact for fact in found)

    def test_nothing_to_say(self, tmp_path: Path) -> None:
        assert infrastructure.collect(scan(tmp_path, {"main.py": ""})) is None


class TestCI:
    def test_github_workflows_with_their_triggers_and_commands(self, tmp_path: Path) -> None:
        long = "uv run pytest -k " + "x" * 120
        workflow = (
            """
name: CI
on:
  push:
  pull_request:
jobs:
  test:
    steps:
      - uses: actions/checkout@v4
      - run: |
          uv sync --locked
          echo done
      - run: """
            + long
            + """
  deploy:
    steps:
      - uses: some/deploy@v1
"""
        )
        files = scan(tmp_path, {".github/workflows/ci.yml": workflow})

        assert facts(ci.collect(files)) == [
            "GitHub Actions, .github/workflows/ci.yml: CI, on push, pull_request",
            f".github/workflows/ci.yml job test: `uv sync --locked`; `{long[:99]}…`",
            ".github/workflows/ci.yml job deploy: no shell commands",
        ]

    def test_gitlab_and_other_systems(self, tmp_path: Path) -> None:
        gitlab = """
stages: [test]
variables: {X: "1"}
.template: {script: [hidden]}
unit:
  stage: test
  script: [npm ci, npm test]
"""
        files = scan(tmp_path, {".gitlab-ci.yml": gitlab, "Jenkinsfile": ""})

        assert facts(ci.collect(files)) == [
            "GitLab CI, .gitlab-ci.yml: stages test",
            ".gitlab-ci.yml job unit: `npm ci`; `npm test`",
            "Jenkins: Jenkinsfile",
        ]

    def test_a_broken_workflow_still_counts(self, tmp_path: Path) -> None:
        files = scan(tmp_path, {".github/workflows/bad.yml": "jobs: [unclosed"})

        assert facts(ci.collect(files)) == ["GitHub Actions, .github/workflows/bad.yml: bad"]

    def test_no_ci(self, tmp_path: Path) -> None:
        assert ci.collect(scan(tmp_path, {"main.py": ""})) is None


class TestChurn:
    def test_commits_and_the_files_changed_most(self, tmp_path: Path) -> None:
        init(tmp_path)
        commit(tmp_path, {"app.py": "1", "queue.py": "1"})
        commit(tmp_path, {"queue.py": "2"})
        commit(tmp_path, {"queue.py": "3", "gone.py": "x"})
        (tmp_path / "gone.py").unlink()
        commit(tmp_path, {})

        found = facts(churn.collect(RepoFiles.scan(tmp_path)))

        assert found[0].startswith("4 commits in the last 90 days, the latest on ")
        assert found[1] == "Most changed files: queue.py (3), app.py (1)"

    def test_paths_are_relative_to_a_repo_inside_a_bigger_one(self, tmp_path: Path) -> None:
        init(tmp_path)
        commit(tmp_path, {"other/x.py": "", "app/src/main.py": "1"})
        commit(tmp_path, {"app/src/main.py": "2"})

        found = facts(churn.collect(RepoFiles.scan(tmp_path / "app")))

        assert found[1] == "Most changed files: src/main.py (2)"

    def test_without_git_there_is_no_history(self, tmp_path: Path) -> None:
        assert churn.collect(scan(tmp_path, {"main.py": ""})) is None


def test_every_collector_handles_an_empty_repo(tmp_path: Path) -> None:
    files = RepoFiles.scan(tmp_path)

    sections = [collect(files) for collect in COLLECTORS]

    assert [section.title for section in sections if section] == ["Tests"]
