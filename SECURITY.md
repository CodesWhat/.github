# Security policy

## Supported versions

CodesWhat ships security fixes for the latest maintained release or branch of
each project. A repository-specific policy, release notes, or README may define
a narrower support window and takes precedence over this organization default.

| Release or branch | Supported |
| --- | --- |
| Latest maintained release or branch | Yes |
| Older or unmaintained releases and branches | No |

For a project that does not publish versioned releases, support applies to the
latest commit on its documented maintained branch.

## Reporting a vulnerability

Do not open a public GitHub issue for a suspected security vulnerability.

Open this repository's **Security** tab and select **Report a vulnerability**
to use GitHub private vulnerability reporting when it is enabled. Otherwise,
email **<security@codeswhat.com>**. Include the affected version or commit,
minimal reproduction steps, observed and expected behavior, and your assessment
of the impact. Redact credentials, private data, and identifying environment
details.

You can expect:

- acknowledgement within 48 hours;
- a status update within 7 days; and
- a fix or mitigation as soon as feasible, depending on severity and release
  safety.

CodesWhat coordinates disclosure with the reporter and credits reporters in
release notes unless they prefer to remain anonymous.

## Security scope

Unless a repository-specific policy says otherwise, the following are in
scope:

- source code and configuration maintained in the repository;
- artifacts published by CodesWhat from that repository; and
- repository-owned build, test, and release automation.

Third-party services, dependencies, and deployments are out of scope unless the
repository introduces or amplifies the reported impact. If the boundary is
unclear, report the issue privately and let the maintainers triage it.
