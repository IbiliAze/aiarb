"""What the app is written in and built on."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from aiarb.scan.files import MAX_FILES
from aiarb.scan.manifests import manifests
from aiarb.scan.section import Section, count

if TYPE_CHECKING:
    from aiarb.scan.files import RepoFiles

LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".cs": "C#",
    ".php": "PHP",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".sh": "Shell",
}
"""Source files by extension. Markup, data and config are left out."""

FRAMEWORKS = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "celery": "Celery",
    "sqlalchemy": "SQLAlchemy",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "@angular/core": "Angular",
    "@remix-run/node": "Remix",
    "express": "Express",
    "fastify": "Fastify",
    "@nestjs/core": "NestJS",
    "@prisma/client": "Prisma",
    "rails": "Rails",
    "sinatra": "Sinatra",
    "github.com/gin-gonic/gin": "Gin",
    "github.com/labstack/echo/v4": "Echo",
    "github.com/gofiber/fiber/v2": "Fiber",
    "actix-web": "Actix Web",
    "axum": "Axum",
    "rocket": "Rocket",
    "laravel/framework": "Laravel",
    "symfony/framework-bundle": "Symfony",
}

RUNTIMES = {"npm": "Node"}
"""The runtime a manifest's version is for, where it is not named for its ecosystem."""

VERSION_FILES = (".python-version", ".nvmrc", ".node-version", ".ruby-version", ".tool-versions")

TOP_LANGUAGES = 6


def collect(files: RepoFiles) -> Section | None:
    facts: list[str] = []
    languages = Counter(
        LANGUAGES[path.suffix.lower()] for path in files.paths if path.suffix.lower() in LANGUAGES
    )
    if languages:
        facts.append(
            "Languages: "
            + ", ".join(
                f"{name} ({count(files, 'file')})"
                for name, files in languages.most_common(TOP_LANGUAGES)
            )
        )
    found = manifests(files)
    frameworks = sorted(
        {
            FRAMEWORKS[name]
            for manifest in found
            for name in manifest.all_dependencies
            if name in FRAMEWORKS
        }
    )
    if frameworks:
        facts.append(f"Frameworks: {', '.join(frameworks)}")
    runtimes = [
        f"{RUNTIMES.get(manifest.ecosystem, manifest.ecosystem)} {manifest.runtime} "
        f"({manifest.path})"
        for manifest in found
        if manifest.runtime
    ]
    for path in files.named(*VERSION_FILES):
        text = files.read(path)
        pinned = "; ".join(line.strip() for line in (text or "").splitlines() if line.strip())
        if pinned:
            runtimes.append(f"{pinned} ({path})")
    if runtimes:
        facts.append(f"Runtime versions: {', '.join(runtimes)}")
    if files.truncated:
        facts.append(f"Only the first {MAX_FILES} files were scanned.")
    return Section("Stack", tuple(facts)) if facts else None
