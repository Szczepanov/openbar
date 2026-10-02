# ADR-0010: Stable aggregate CI gate and default-branch protection

- Status: Accepted
- Date: 2026-10-02

## Context

OpenBar treats deterministic validation as part of the product, but repository CI is useful as a
merge control only when GitHub repository settings actually require it.

The CI workflow contains several internal jobs whose names and decomposition may evolve as M0
validation changes. Requiring every internal job directly in branch protection would couple
repository settings to workflow implementation details and make routine CI refactors require
matching settings changes.

At the time of this decision, `main` has no branch protection or ruleset and no required status
checks. A versioned workflow change cannot by itself enforce merge policy.

## Decision

### Stable required-check contract

The GitHub Actions job named **`CI Gate`** is the stable status-check contract for changes targeting
`main`.

`CI Gate`:

- runs with `if: always()` after all required internal CI jobs;
- fails unless every required upstream job concludes with `success`;
- currently aggregates:
  - `Rust / Linux`;
  - `Rust / Windows`;
  - `Dependency Policy`;
  - `Validation Tooling`.

Internal job names and composition may change without changing repository protection as long as the
aggregate `CI Gate` contract remains stable and continues to depend on every required gate.

### Events

Required CI runs on:

- pull requests targeting `main`;
- pushes to `main`;
- merge-queue groups through `merge_group: checks_requested`;
- explicit `workflow_dispatch` runs for diagnostics.

Superseded pull-request runs may be cancelled. Push and merge-group runs are not cancelled merely
because a newer run starts.

The `merge_group` trigger is retained even before merge queue is enabled so a future queue does not
silently wait for a required check that never runs.

### Repository settings

After the workflow containing `CI Gate` is present on `main`, configure a branch protection rule
or branch ruleset for the default branch to:

1. require a pull request before merging;
2. require the **`CI Gate`** status check;
3. when the UI permits it, bind the required check to the GitHub Actions app/source rather than
   accepting an identically named status from any source;
4. decide explicitly whether branches must be up to date before merge.

Do not separately require each internal CI job unless this ADR is revisited.

Repository settings are external state. Maintainers should periodically verify that `main` is still
protected and that `CI Gate` is still required.

## Consequences

### Positive

- branch/ruleset configuration depends on one stable check name;
- internal CI jobs can be reorganized without repeatedly editing protection settings;
- failure, cancellation, or skipping of a required upstream job makes the aggregate gate fail;
- merge-queue compatibility is explicit.

### Limitations

- this ADR and workflow do not themselves protect `main`; GitHub repository settings must be
  configured after merge;
- a workflow-changing PR can change what `CI Gate` means, so workflow changes still require normal
  code review;
- requiring only the aggregate gate makes its dependency list security- and governance-relevant.

## Validation

Changes to the gate should be verified by an actual GitHub Actions pull-request run. The expected
check name is exactly `CI Gate`, and the run should show all four required upstream jobs completing
successfully before the aggregate job succeeds.

Revisit this ADR if OpenBar adopts a merge queue, reusable required workflows, organization-level
rulesets, additional required jobs, or a different CI provider.
