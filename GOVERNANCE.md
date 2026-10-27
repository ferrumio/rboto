# Project governance

rboto is maintained by [Leandro Damascena](https://github.com/leandrodamascena)
under the ferrumio organization. The maintainer is responsible for project direction,
review and merge decisions, releases, and coordinating security reports.
Review requests are routed through [CODEOWNERS](.github/CODEOWNERS).

## Decisions and contributions

Anyone can report a bug, propose a feature, or submit a pull request. Use the issue
templates to explain the problem and proposed behavior. Changes follow
[CONTRIBUTING.md](CONTRIBUTING.md), including code generation and validation rules.

Discuss changes that affect the public API, supported platforms, architecture, or release
process in an issue or pull request. Record accepted architecture decisions in
[ADR](ADR/). A new decision supersedes an accepted record rather than rewriting its
history. The maintainer makes the final decision after considering the technical evidence
and contributor feedback.

## Review and releases

Pull requests are the normal path for changes to `main`. Review considers correctness,
compatibility, test results, and the maintenance cost of generated and handwritten code.
Automated reviews support this process; they do not replace maintainer judgment.
CODEOWNERS routes reviews but does not itself enforce branch protection.

The maintainer coordinates version bumps and release tags. The facade and service packages
use the same version, and the release workflow builds and smoke-tests the supported wheel
matrix before publication. Only the latest release receives fixes while rboto is below 1.0.

## Community and security

Participants follow the [Code of conduct](CODE_OF_CONDUCT.md), including the private
reporting and moderation process. Keep discussions respectful and focused on the work,
and explain disagreements with examples and evidence.

Report vulnerabilities through the private channel in [SECURITY.md](SECURITY.md).
Scorecard and CodeQL results are available from the repository's Actions and Security tabs.
