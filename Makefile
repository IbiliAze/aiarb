# Development and release tasks. `make help` lists them.
#
# `make publish` uploads the release tagged for the version in pyproject.toml
# to the GitLab package registry. It needs a GitLab token that can write to the
# registry, as UV_PUBLISH_USERNAME and UV_PUBLISH_PASSWORD, exported or in .env.
# Files the registry already has are skipped, so publishing again is harmless.
# The registry is read with the same token: uv sends the publish credentials
# only with uploads.

VERSION := $(shell sed -n 's/^version = "\(.*\)"$$/\1/p' pyproject.toml)
TAG := v$(VERSION)
REGISTRY_PATH := gitlab.com/api/v4/projects/projects4833423%2Faiarb/packages/pypi
REGISTRY := https://$(REGISTRY_PATH)
RELEASE := dist/release

.DEFAULT_GOAL := help
.PHONY: help sync lint test check build publish

help: ## List the tasks
	@awk 'BEGIN {FS = ":.*## "} /^[a-z]+:.*## / {printf "  make %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Install the project and its dev tools
	uv sync

lint: ## Lint, check formatting and type-check
	uv run ruff check
	uv run ruff format --check
	uv run mypy

test: ## Run the tests, which make no API calls
	uv run pytest

check: lint test ## Lint and test, as CI does

build: ## Build the wheel and sdist from the working tree, into dist/
	rm -rf dist
	uv build --out-dir dist

publish: ## Publish the tagged release of this version to the GitLab registry
	@git rev-parse -q --verify "refs/tags/$(TAG)" >/dev/null || \
		{ echo "No tag $(TAG) for version $(VERSION): tag the release commit first."; exit 1; }
	rm -rf $(RELEASE) && mkdir -p $(RELEASE)/src
	git archive $(TAG) | tar -x -C $(RELEASE)/src
	uv build $(RELEASE)/src --out-dir $(RELEASE)
	@set -a; [ -f .env ] && . ./.env; set +a; \
	[ -n "$$UV_PUBLISH_USERNAME" ] && [ -n "$$UV_PUBLISH_PASSWORD" ] || \
		{ echo "Set UV_PUBLISH_USERNAME and UV_PUBLISH_PASSWORD to a GitLab token that can write to the registry."; exit 1; }; \
	uv publish $(RELEASE)/*.whl $(RELEASE)/*.tar.gz \
		--publish-url "$(REGISTRY)" \
		--check-url "https://$$UV_PUBLISH_USERNAME:$$UV_PUBLISH_PASSWORD@$(REGISTRY_PATH)/simple"
