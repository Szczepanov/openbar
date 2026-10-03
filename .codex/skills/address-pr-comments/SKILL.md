---
name: address-pr-comments
description: Address review comments on open pull requests in OpenBar. Read comments, formulate minimal targeted fixes, verify against OpenBar test and schema gates, and push updates.
argument-hint: "Optionally specify PR number, reviewer, or file to focus on"
---

# Address PR Comments: OpenBar Review Workflow

Read the active pull request, identify unresolved review comments and feedback, implement the requested changes, run OpenBar's validation gates, and reply to/resolve the review threads.

## When to Use

- Reviewer or CI has left comments or change requests on an open pull request.
- You need to address inline review threads on OpenBar code, schemas, or docs.
- You want to update an existing PR branch with verified fixes.

## Procedure

### 1. Identify the Active PR and Branch

- Check the current branch: `git rev-parse --abbrev-ref HEAD`.
- If an issue or PR number `N` is provided:
  ```bash
  gh pr view <N> --json number,title,headRefName,baseRefName,url,state
  ```
- If on the PR branch directly:
  ```bash
  gh pr view --json number,title,headRefName,baseRefName,url,state
  ```

### 2. Read Review Comments and Feedback

Fetch general comments and inline review discussions:

```bash
gh pr view <N> --comments
```

For detailed inline review comments:
```bash
gh api repos/:owner/:repo/pulls/<N>/comments --jq '.[] | {id: .id, path: .path, line: .line, body: .body, user: .user.login}'
```

Group unresolved feedback by file and subsystem (`crates/openbar-core`, `crates/openbar-tracking`, `apps/openbar-cli`, `validation/`, `docs/`).

### 3. Plan Minimal, Targeted Fixes

- Read each unresolved comment carefully alongside the referenced code.
- Formulate the minimal correct fix for each comment without expanding scope.
- **Check OpenBar invariants**: Ensure fixes do not inadvertently violate:
  - Raw observations preserved (never overwritten).
  - Lost tracking remains explicit (no fabricated coordinates or silent gap interpolation).
  - Timestamps remain authoritative.
  - Determinism (`BTreeMap`, no wall-clock/random data).
  - NaN/$\pm\infty$ strict rejection.
  - Pixel-centre coordinate convention (ADR-0007).
  - Clean-room discipline (MIT licence, no competitor code copied).
- If a comment requests a change that violates an accepted ADR or measurement invariant, prepare an explanation for the reviewer instead of making the change blindly.

### 4. Implement Fixes

- Apply changes directly on the PR worktree branch.
- Keep diffs tight and focused strictly on the requested feedback.
- If serialized types change, update the corresponding `validation/schema/*.schema.json` and ensure version constants reflect the change.

### 5. Run OpenBar Verification Gates

All changes must pass OpenBar repository gates before pushing:

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo build --locked --workspace --all-targets --all-features
cargo test --locked --workspace --all-targets --all-features
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
```

If the PR touched tracker/filter/analyze CLI paths, run the relevant smoke checks:
```bash
cargo run --locked -p openbar-cli -- analyze --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --plate-diameter-m 0.45 --tracker template --filter raw --kinematics-max-gap-s 0.2 --kinematics-min-confidence 0 --output target/analyze-smoke.json
```

### 6. Commit and Push

Commit with a clear message referencing the issue/PR:
```bash
git commit -m "fix(<scope>): address review feedback on #<N>"
git push origin <branch-name>
```

### 7. Reply and Resolve Threads

- Reply to the reviewer comments using `gh`:
  ```bash
  gh pr comment <N> --body "Addressed review comments: ..."
  ```
- For inline comments, reply via GitHub API or mark resolved if permissions allow.

### 8. Summary

Provide a concise summary:
- Comments addressed and files modified.
- Explanations for any comments intentionally declined or clarified.
- Verification command outputs confirming clean test pass.
