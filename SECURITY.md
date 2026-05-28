# Security Policy

## Disclaimer

This project is **vibe-coded** — built rapidly with AI assistance. It is not audited and carries no security guarantees. Do not rely on scan results as the sole basis for security decisions.

## Reporting a Vulnerability

Report security vulnerabilities by [opening a GitHub Issue](https://github.com/mgm-sec/DASBOM-pub/issues).

**Response time:** Best effort — this is a solo side project with no guaranteed SLA.

## Scope

The following are in scope for responsible disclosure:

- **Docker image** — vulnerabilities in the base image, installed packages, or container configuration
- **Web server / UI** — Flask server, SSE endpoint, input handling (`server.py`)
- **Pipeline scripts** — the bash pipeline clones and executes content from external repositories; issues that allow malicious repo content to escape the intended execution context

## Out of Scope

Vulnerabilities in upstream dependencies (Flask, syft, gh CLI, etc.) should be reported directly to those projects.

## Notes

- The pipeline makes outbound network requests to GitHub, OSV.dev, npm, PyPI, RubyGems, crates.io, and other registries at runtime.
- The Docker container runs as a non-root user with dropped Linux capabilities.
- Dependencies are version-pinned and hash-verified where applicable.
