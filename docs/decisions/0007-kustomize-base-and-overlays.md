# 0007. Kubernetes: Kustomize base + overlays

- **Status:** accepted
- **Date:** 2026-10-31

## Context
The manifests had development values, and CI made them production by editing files during deployment
(a `sed` over the ConfigMap, removing `secret.yaml`). What production runs could only be found in the CI script.

## Decision
`deploy/k8s/base` holds what is the same everywhere; `overlays/dev` adds the dev Secret,
`overlays/prod` adds production settings (`production.yaml`) and no Secret (CI creates it from GitHub
Secrets). CI only sets the image tags.

## Consequences
- Each environment is readable in git; a renamed key fails loudly instead of a `sed` silently not matching.
- Adding staging is one more overlay.
- Kustomize can't load files from outside its folder, so the observability configs live in
  `deploy/observability` with their own kustomization, included by the base.
