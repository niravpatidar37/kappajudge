# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub's private vulnerability reporting:
[Security → Report a vulnerability](https://github.com/niravpatidar37/kappajudge/security/advisories/new).
Do not open a public issue for a security problem.

Include what you found, how to reproduce it, and the impact you expect. You should get a first reply
within 7 days. This is a single-maintainer portfolio project, so there is no bug bounty.

## Scope

In scope: the judge and its output validation, the release gate (`src/judgecal/gate.py` and the
`release-gate` CI job), the LLM client's spend cap and kill switch, the labeling UI and its database
access, the Terraform in `infra/terraform`, and the CI/CD workflows.

Known residual risks are listed in the README's [Security model](README.md#security-model) section,
for example that a forged eval record passes the evidence-only gate unless the live gate runs.

## Supported versions

Only the latest commit on `main` is supported.
