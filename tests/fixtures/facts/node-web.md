# Repo facts

## Stack

- Languages: TypeScript (6 files)
- Frameworks: Next.js, Prisma, React
- Runtime versions: Node >=20 (package.json), 20.18.0 (.nvmrc)

## Dependencies

- package.json (npm): runtime: next, react, react-dom, @prisma/client, zod; dev: typescript, vitest, @playwright/test, prisma
- Lockfiles: pnpm-lock.yaml (pnpm)

## Tests

- Test files: 2, against 4 source files.
- Where they are: components/ (1), e2e/ (1)
- Test tools: Playwright, Vitest

## Infrastructure

- Hosting and deploy config: vercel.json
- Database schema: prisma/schema.prisma
- Migrations: prisma/migrations/ (1 file)

## CI

- GitHub Actions, .github/workflows/test.yml: Test, on push
- .github/workflows/test.yml job unit: `pnpm install --frozen-lockfile`; `pnpm test`
- .github/workflows/test.yml job e2e: `pnpm install --frozen-lockfile`; `pnpm e2e`
