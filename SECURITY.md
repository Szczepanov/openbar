# Security policy

## Supported versions

OpenBar is pre-alpha and has no releases. Only the `main` branch is supported. Fixes land there
and nothing is backported.

## Reporting a vulnerability

Please do **not** open a public issue, pull request or discussion for a suspected vulnerability.

Report it privately through GitHub instead: go to the repository's **Security** tab, choose
**Report a vulnerability**, and include:

- the affected commit, file or command;
- steps or an input file that reproduce the problem;
- what an attacker could achieve.

This is a small project run on a best-effort basis. Expect an acknowledgement within a week,
but there is no guaranteed fix timeline. Once a fix is available, the advisory is published
with credit to the reporter unless you ask to stay anonymous.

## Scope

In scope:

- code in this repository: the Rust crates, the CLI and the Python validation tooling;
- handling of untrusted inputs, such as analysis, seed, annotation, manifest and benchmark JSON,
  and video files once decoding lands;
- how the CLI invokes external programs, such as FFmpeg (planned in issue #40);
- the CI workflows and dependency supply chain.

Out of scope:

- vulnerabilities in FFmpeg, the Rust toolchain or third-party crates. Report those upstream.
  Tell us too if OpenBar uses them in a way that makes the problem exploitable.
- measurement-accuracy problems with no security impact. Open a normal issue for those.
