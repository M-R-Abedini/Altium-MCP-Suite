# Security Policy

## Supported versions

`eda-agent` is in alpha (current version 0.2.x). Only the latest released
version receives security fixes. There are no LTS branches.

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Email reports to **info@salitronic.com** with:

- A description of the issue and its impact
- Steps to reproduce, including a minimal `request.json` payload or MCP
  call sequence if relevant
- The affected `eda-agent` version and Altium Designer version
- Any suggested mitigation

You should receive an acknowledgement within 7 days. If the report is
confirmed, a fix will be prepared and released; coordinated disclosure
timing will be agreed with the reporter.

## Scope

In scope:

- The Python MCP server (`src/eda_agent/`)
- The DelphiScript bridge (`scripts/altium/`)
- The Inno Setup installer (`installer/`)
- The file-based IPC protocol between the two sides

Out of scope:

- Vulnerabilities in Altium Designer itself: report those to
  Altium directly
- Vulnerabilities in upstream Python packages: report those to the
  package maintainers (we will bump pins once a fix is available)
- Issues that require an attacker who already has interactive access
  to the host Windows account running Altium

## Threat model

This agent runs locally and trusts the host machine. The IPC channel is a
shared workspace directory under the user profile and is not authenticated
beyond filesystem permissions. The Pascal side executes anything the Python
side sends. Both sides assume that whoever can write to the workspace
directory is authorised to drive Altium.

Do not expose the workspace directory or the MCP stdio endpoint to
untrusted callers.
