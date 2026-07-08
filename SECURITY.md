# Security Policy

## Supported Versions

Currently, only the latest release on the `main` branch is supported with security updates. 

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| `< 1.0` | :x:                |

## Reporting a Vulnerability

We take the security of MemMCP incredibly seriously. Because this toolkit orchestrates memory for autonomous agent swarms, any vulnerability that allows for Indirect Prompt Injection, context poisoning, or arbitrary execution is treated as a critical, severity-1 incident.

**DO NOT** report security vulnerabilities via public GitHub issues.

Instead, please email vulnerabilities directly to the maintainers or use GitHub's private vulnerability reporting feature.

Please include:
1. A description of the vulnerability.
2. The exact steps to reproduce it.
3. The potential impact (e.g., "Allows an attacker to bypass the XML RAG boundaries").

We will acknowledge receipt of your vulnerability report within 48 hours and strive to issue a patch and security advisory as rapidly as possible.
