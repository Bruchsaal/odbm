# Security Policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/Bruchsaal/odbm/security/advisories/new)
rather than opening a public issue.

This is a spare-time project, so please allow a reasonable window for a fix
before public disclosure.

## Threat model

ODBM is a **single-user, local diagnostic tool**. Understanding what it
deliberately does not do matters more than any individual bug:

- **There is no authentication.** Anyone who can reach the port can run
  arbitrary SQL — including DDL and DML — as the configured database user, with
  no audit trail beyond Oracle's own.
- **It binds `127.0.0.1` by default.** This is the security boundary. Setting
  `ODBM_HOST=0.0.0.0` exposes unauthenticated arbitrary SQL execution to the
  network; the application logs a warning when you do. Do not run it that way on
  an untrusted network, and do not put it behind a plain reverse proxy expecting
  that to make it safe — it needs authentication in front of it, at minimum.
- **Credentials may be highly privileged.** Connections can be configured as
  `SYSDBA`. Prefer a least-privilege monitoring account with `SELECT_CATALOG_ROLE`.

## Credential handling

- Passwords are encrypted at rest with Fernet (AES-128-CBC + HMAC) using a key
  in `.odbm_key`, created mode `0600` beside `connections.json`, itself `0600`.
- `.odbm_key` is **not** a secret store. It sits next to the data it protects,
  so it defends against casual disclosure — a shared backup, a copied config
  file — not against an attacker with read access to the directory.
- Passwords are never sent to the browser. `GET /api/config` returns a
  `has_password` flag; a blank password on save means "keep the stored one".
- Credentials are never bundled into released binaries, and `connections.json`
  is not tracked in git.

### Windows

The `0600` modes above are POSIX. On Windows `os.chmod` only toggles the
read-only attribute, so `connections.json` and `.odbm_key` inherit whatever
NTFS ACL their parent directory carries — they are **not** restricted to your
account by ODBM. If other accounts can read the directory you run ODBM from,
they can read your encryption key and the encrypted credentials beside it.

Run ODBM from a directory only your account can read, for example under
`%LOCALAPPDATA%`, rather than a shared or world-readable location.

## Scope

In scope: credential disclosure, escaping and injection issues, anything
allowing SQL execution beyond what the UI intends, and dependency
vulnerabilities in shipped components.

Out of scope: the absence of authentication and the ability to run arbitrary SQL
via the SQL tab. Both are the tool's purpose, and are the reason it binds to
localhost.
