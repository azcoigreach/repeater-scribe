# Security policy

## Supported versions

Security fixes are supported on the latest release. Older releases may be asked
to upgrade before a fix is backported.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting or contact the maintainers
privately. Do not open a public issue for security-sensitive reports. Include the
affected version, deployment mode, reproduction steps, and impact; omit real AMI,
OIDC, QRZ, and API credentials.

## Security priorities

- Avoid exposing secret material through the API or UI.
- Keep the application non-root in Docker.
- Validate file paths before serving audio or archived content.
- Treat transcript content as user data and escape it in HTML output.
- Do not expose Asterisk control or AMI administration interfaces.
- Keep the application port private and expose only the authenticated TLS proxy.
- Require OIDC MFA for operators and administrators.
- Rotate session, OIDC, API-token, QRZ, and AMI secrets after suspected access.
- Preserve security audit records and relevant proxy logs during an incident.

## Operator response

If compromise is suspected, first set `ASLT_AMI_CONTROL_ENABLED=false`, restart
the application, and restrict the public proxy. Revoke active API tokens and OIDC
sessions, rotate the AMI credential, then rotate application and identity-provider
secrets. Restore from a verified backup if application data integrity is in doubt.

See [Internet security and operations](docs/security.md) for the deployment,
backup, verification, and incident-response checklist.
