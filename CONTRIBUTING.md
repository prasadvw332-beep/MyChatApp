# Contributing

How changes get into this repository, so that the history explains itself later.

## 1. One branch per change

Never commit to `main` directly. Start a branch from an up-to-date `main`:

```bash
git switch main && git pull
git switch -c feature/rate-limiting
```

| Prefix | For |
|---|---|
| `feature/` | new behaviour (`feature/agent-mcp`) |
| `fix/` | a bug (`fix/nginx-backend-dns`) |
| `refactor/` | structure changes, same behaviour (`refactor/backend-structure`) |
| `chore/` | tooling, CI, deployment (`chore/k8s-overlays`) |
| `docs/` | documentation only |

One branch = one topic. If a change depends on another unmerged branch, branch from that one and say
so in the PR ("based on #12").

## 2. Commits that explain themselves

```
Add LLM token and cost tracking (Prometheus, PostgreSQL, Grafana)     <- subject: what, imperative, < 72 chars

Why: we had no idea how many tokens each feature uses ...             <- the problem
What: ...                                                             <- what changed, by area
Known gaps: ...                                                       <- what it does NOT do
Verified: 57 tests pass; end to end in docker compose ...             <- how you know it works
```

Move files with `git mv` so their history follows them.

## 3. Checks before pushing

```bash
make check          # every linter and test, the same as CI
make hooks          # once: run the fast checks automatically on each commit
```

Without `make`: see the commands in the [Makefile](Makefile). CI runs the same checks on every pull
request; images are only built and deployed when they pass.

## 4. Pull request

Push the branch and open a PR into `main`. The template asks for the why, the what, how it was tested,
and anything the reviewer should look at. Merge when CI is green and it's reviewed; delete the branch after.

## 5. Keep the docs true

In the same PR as the code:
- a new setting -> [docs/configuration.md](docs/configuration.md) and `.env.example`
- a new folder or kind of module -> [docs/architecture.md](docs/architecture.md) ("where new things go")
- a significant choice (a new dependency, a pattern others must follow) -> a record in
  [docs/decisions/](docs/decisions/)
- anything a user or operator would notice -> [CHANGELOG.md](CHANGELOG.md), under *Unreleased*
