# Security Policy

## About CloudWire's security model

CloudWire is a **read-only** local tool. It:

- Never modifies AWS resources (only `Describe*`, `List*`, and `Get*` API calls)
- Never transmits data outside your machine
- Binds to `127.0.0.1` by default (warns if you bind to `0.0.0.0`)
- Never stores AWS credentials -- it uses your existing credential chain

The graph data (including ARNs, VPC CIDRs, security group rules) is held in-memory only and discarded when the process exits.

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest (0.2.x) | Yes |
| < 0.2.0 | No |

## Reporting a vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public GitHub issue.**
2. Use [GitHub's private vulnerability reporting](https://github.com/Himanshu-370/cloudwire/security/advisories/new) to submit your report.
3. Include: affected version, steps to reproduce, potential impact.

You can expect an initial response within **72 hours**. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Scope

Security issues we care about:

- Unintended write operations to AWS accounts
- Data exfiltration from the local server (e.g., CSRF attacks when bound to localhost)
- Path traversal or injection in the API or file upload endpoints
- Credential leakage in logs, error messages, or API responses

Out of scope:

- Vulnerabilities that require the attacker to already have access to the local machine
- Denial of service against the local-only server
- Issues in upstream dependencies (report those to the relevant project)
