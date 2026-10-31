# 0005. Login session in an httpOnly cookie

- **Status:** accepted
- **Date:** 2026-10-31

## Context
The frontend needs to stay logged in. Common options: a token kept in localStorage and sent as a header,
or a cookie.

## Decision
A signed **JWT in an httpOnly cookie** (`backend/app/core/security.py`), `Secure` in production. The
frontend never sees the token; it only asks `/auth/me` who is logged in.

## Consequences
- JavaScript (including injected script from an XSS bug) cannot read the session.
- CORS must list exact origins (cookies forbid `*`); in production nginx serves frontend and API from one
  origin, so CORS doesn't apply at all.
- Every replica must share `JWT_SECRET`, or a login made on one pod is rejected by another; production
  refuses to start without it.
