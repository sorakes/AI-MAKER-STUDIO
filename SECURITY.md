# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through [GitHub's private vulnerability reporting](https://github.com/sorakes/AI-MAKER-STUDIO/security/advisories/new) instead of opening a public issue.

Include what you found, how to reproduce it and the version or commit you tested. You'll get an answer as soon as possible, and a fix will be released before the details are made public.

## Supported versions

Security fixes go into the latest release and the `main` branch.

## Running it safely

- Set a fixed `SECRET_KEY` in production and keep it outside the data volume.
- Put the studio behind a TLS proxy (Caddy, Traefik, Nginx) and set `COOKIE_SECURE=1`.
- Only `/pub/` is reachable through the video editing tunnel; keep it that way if you change the tunnel setup.
