# Contributing to CodesWhat projects

Bug fixes, focused features, tests, and documentation improvements are welcome.
Repository-specific instructions take precedence over this organization
default.

## Before starting

1. Read the repository's `README.md`, `AGENTS.md`, and development or release
   documentation.
2. Search existing issues and pull requests for the same problem.
3. Open an issue before starting a large feature, breaking change, architecture
   change, or release-process change.
4. From the documented active development or integration branch, create a
   focused branch in your fork.

Do not commit credentials, private data, local planning files, editor state, or
generated artifacts that the repository does not track.

## Making a change

- Keep the change focused on one concern.
- Follow the repository's existing language, formatting, and architecture
  conventions.
- Add regression tests for bug fixes and coverage for non-trivial behavior.
- Update documentation and changelog entries when public behavior changes.
- Run the exact formatter, lint, test, build, and local hook commands documented
  by the repository.

## Commits

Use plain Conventional Commits without emoji:

```text
<type>(<optional scope>): <description>
```

Allowed types are
`feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert`. Keep the subject
concise and describe the change in the imperative mood.

## Pull requests

- Select the repository's documented target branch. Do not assume the GitHub
  default branch is the correct target.
- Explain the problem, the change, and the verification performed.
- Call out security, compatibility, migration, or release effects.
- Include tests and documentation, or explain why they do not apply.
- Wait for all required checks and reviews before merge.

Maintainers may ask for changes or decline work that does not fit the project's
scope. Contributions are licensed under the license of the repository receiving
them.

Report suspected vulnerabilities privately as described in
[`SECURITY.md`](SECURITY.md), never in a public issue or pull request.
