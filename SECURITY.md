# Security policy

This repository is the production application of a real business: the
internal plate-production and point-of-sale system of INGSE (Colombia).
Please handle any security issue with care.

## Reporting a vulnerability

Report vulnerabilities **privately**, through GitHub's private vulnerability
reporting: open the repository's **Security** tab and click
**"Report a vulnerability"**.

**Never** report a vulnerability in a public issue, pull request or
discussion.

## Supported versions

Only the `main` branch is supported. It is what runs in production; fixes
are made there only.

## Testing

Do **not** test against the production site or its infrastructure (servers,
databases, domains or accounts). No scanning, fuzzing, load testing or
exploitation attempts of any kind. Run the application locally (see
`README.md`) to reproduce an issue.
