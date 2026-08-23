# Security policy

## Supported versions

This project is in early development and is intended for self-hosted deployments. Security issues are handled as they are reported.

## Reporting a vulnerability

Please report suspected security issues privately via the project maintainers. Do not open a public issue for security-sensitive reports.

## Security priorities

- Avoid exposing secret material through the API or UI.
- Keep the application non-root in Docker.
- Validate file paths before serving audio or archived content.
- Treat transcript content as user data and escape it in HTML output.
- Do not expose Asterisk control or AMI administration interfaces.
