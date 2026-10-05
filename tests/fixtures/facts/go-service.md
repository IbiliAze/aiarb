# Repo facts

## Stack

- Languages: Go (3 files)
- Frameworks: Gin
- Runtime versions: Go 1.23 (go.mod)

## Dependencies

- go.mod (Go): runtime: github.com/gin-gonic/gin, github.com/redis/go-redis/v9, github.com/stretchr/testify; dev: none
- Lockfiles: go.sum (Go modules)

## Tests

- Test files: 1, against 2 source files.
- Where they are: internal/queue/ (1)
- Test tools: go test, testify

## Infrastructure

- Containers: Dockerfile
- Base images: gcr.io/distroless/static-debian12, golang:1.23
- Kubernetes: deploy/k8s/deployment.yaml, deploy/k8s/service.yaml
- Terraform: infra/ (2 files)

## CI

- GitLab CI, .gitlab-ci.yml: stages test, build, deploy
- .gitlab-ci.yml job test: `go vet ./...`; `go test ./...`
- .gitlab-ci.yml job build: `docker build -t queue .`
- .gitlab-ci.yml job deploy: `kubectl apply -f deploy/k8s`
