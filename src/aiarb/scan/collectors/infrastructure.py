"""How the app is built, deployed and configured, and where its schema changes live."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from aiarb.scan.section import Section

if TYPE_CHECKING:
    from pathlib import PurePosixPath

    from aiarb.scan.files import RepoFiles

PLATFORM_FILES = (
    "fly.toml",
    "vercel.json",
    "netlify.toml",
    "render.yaml",
    "railway.json",
    "railway.toml",
    "wrangler.toml",
    "Procfile",
    "serverless.yml",
    "serverless.yaml",
    "Pulumi.yaml",
    "cdk.json",
)
KUBERNETES_FOLDERS = frozenset({"k8s", "kubernetes", "helm", "charts", "manifests"})
MIGRATION_FOLDERS = ("migrations", "alembic", "db/migrate", "prisma/migrations")
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template")
"""Read for variable names only. A real `.env` is never read."""

LISTED = 8


def collect(files: RepoFiles) -> Section | None:
    facts: list[str] = []

    containers = [
        path
        for path in files.paths
        if path.name.startswith("Dockerfile")
        or path.name.endswith(".dockerfile")
        or re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", path.name)
    ]
    if containers:
        facts.append(f"Containers: {_list(containers)}")
        images = sorted({image for path in containers for image in _base_images(files, path)})
        if images:
            facts.append(f"Base images: {', '.join(images)}")

    kubernetes = [
        path
        for path in files.paths
        if path.name in ("Chart.yaml", "kustomization.yaml")
        or (path.suffix in (".yaml", ".yml") and KUBERNETES_FOLDERS & set(path.parts[:-1]))
    ]
    if kubernetes:
        facts.append(f"Kubernetes: {_list(kubernetes)}")

    terraform = Counter(f"{path.parent}/" for path in files.matching("*.tf"))
    if terraform:
        facts.append(
            "Terraform: "
            + ", ".join(f"{folder} ({count} files)" for folder, count in sorted(terraform.items()))
        )

    platforms = files.named(*PLATFORM_FILES)
    if platforms:
        facts.append(f"Hosting and deploy config: {_list(platforms)}")

    migrations = Counter(
        folder
        for path in files.paths
        for folder in MIGRATION_FOLDERS
        if f"/{folder}/" in f"/{path.parent}/"
    )
    if files.has("prisma/schema.prisma"):
        facts.append("Database schema: prisma/schema.prisma")
    if migrations:
        facts.append(
            "Migrations: "
            + ", ".join(
                f"{folder}/ ({count} files)" for folder, count in sorted(migrations.items())
            )
        )

    for path in files.named(*ENV_EXAMPLES):
        names = _variable_names(files.read(path) or "")
        if names:
            facts.append(f"Environment variables ({path}): {', '.join(names)}")

    return Section("Infrastructure", tuple(facts)) if facts else None


def _base_images(files: RepoFiles, path: PurePosixPath) -> list[str]:
    if not path.name.startswith("Dockerfile") and not path.name.endswith(".dockerfile"):
        return []
    text = files.read(path) or ""
    return [
        found.group(1)
        for found in re.finditer(
            r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", text, re.MULTILINE | re.IGNORECASE
        )
        if found.group(1).lower() != "scratch" and "$" not in found.group(1)
    ]


def _variable_names(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        found = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if found and found.group(1) not in names:
            names.append(found.group(1))
    return names


def _list(paths: list[PurePosixPath]) -> str:
    shown = ", ".join(str(path) for path in paths[:LISTED])
    return f"{shown} (+{len(paths) - LISTED} more)" if len(paths) > LISTED else shown
