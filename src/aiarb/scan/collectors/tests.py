"""How much of the app is tested, where the tests live, and what runs them."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from aiarb.scan.collectors.stack import LANGUAGES
from aiarb.scan.manifests import manifests
from aiarb.scan.section import Section

if TYPE_CHECKING:
    from pathlib import PurePosixPath

    from aiarb.scan.files import RepoFiles

TEST_NAME = re.compile(
    r"""(
        ^test_.+\.py$ | _test\.(py|go)$ | _spec\.rb$
        | \.(test|spec)\.[cm]?[jt]sx?$ | Tests?\.(java|kt|cs|php)$
    )""",
    re.VERBOSE,
)
TEST_FOLDERS = frozenset({"test", "tests", "__tests__", "spec", "specs"})

FRAMEWORKS = {
    "pytest": "pytest",
    "hypothesis": "Hypothesis",
    "jest": "Jest",
    "vitest": "Vitest",
    "mocha": "Mocha",
    "@playwright/test": "Playwright",
    "cypress": "Cypress",
    "@testing-library/react": "Testing Library",
    "rspec": "RSpec",
    "rspec-rails": "RSpec",
    "minitest": "Minitest",
    "phpunit/phpunit": "PHPUnit",
    "github.com/stretchr/testify": "testify",
}
CONFIG_FILES = {
    "pytest.ini": "pytest",
    "conftest.py": "pytest",
    "jest.config.js": "Jest",
    "jest.config.ts": "Jest",
    "vitest.config.ts": "Vitest",
    "vitest.config.js": "Vitest",
    "playwright.config.ts": "Playwright",
    "playwright.config.js": "Playwright",
    "cypress.config.ts": "Cypress",
    "cypress.config.js": "Cypress",
    ".rspec": "RSpec",
    "phpunit.xml": "PHPUnit",
    "phpunit.xml.dist": "PHPUnit",
}

TOP_FOLDERS = 8


def collect(files: RepoFiles) -> Section:
    code = [path for path in files.paths if path.suffix.lower() in LANGUAGES]
    tests = [path for path in code if is_test(path)]
    sources = len(code) - len(tests)
    if not tests:
        facts = [f"No test files found, against {sources} source files."]
    else:
        facts = [f"Test files: {len(tests)}, against {sources} source files."]
        folders = Counter(f"{path.parent}/" for path in tests)
        facts.append(
            "Where they are: "
            + ", ".join(f"{folder} ({count})" for folder, count in folders.most_common(TOP_FOLDERS))
        )
    frameworks = {
        FRAMEWORKS[name]
        for manifest in manifests(files)
        for name in manifest.all_dependencies
        if name in FRAMEWORKS
    }
    frameworks |= {CONFIG_FILES[path.name] for path in files.named(*CONFIG_FILES)}
    if any(path.name.endswith("_test.go") for path in tests):
        frameworks.add("go test")
    if frameworks:
        facts.append(f"Test tools: {', '.join(sorted(frameworks, key=str.lower))}")
    return Section("Tests", tuple(facts))


def is_test(path: PurePosixPath) -> bool:
    return bool(TEST_NAME.search(path.name)) or bool(TEST_FOLDERS & set(path.parts[:-1]))
