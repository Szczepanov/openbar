# Implementation plans

`docs/plans/` contains execution-oriented plans for work that has already been analyzed enough to
sequence concretely, but is not necessarily approved as a production architecture decision.

Use the repository documentation layers consistently:

- `docs/analysis/` — investigation, alternatives, external references and reasoning;
- `docs/plans/` — ordered implementation work packages, dependencies, tests, evidence and decision gates;
- `docs/validation/` — normative measurement/validation contracts;
- `docs/roadmap/` — milestone scope and sequencing;
- `docs/adr/` — durable architecture decisions.

A plan does not override an accepted ADR, schema, validation contract or scope fence.

## Recommended plan structure

Plans should be executable by another engineer/agent without reconstructing the original reasoning.
Include, when applicable:

1. context and current state;
2. goal and explicit non-goals;
3. governing architecture/measurement constraints;
4. dependencies and prerequisites;
5. ordered work packages;
6. likely files/modules affected;
7. deterministic tests and validation commands;
8. evidence artifacts to retain;
9. decision/promotion gates;
10. stop conditions;
11. rollout/integration path;
12. documentation/issues/ADRs that must be updated if the work is promoted.

For research work, explicitly distinguish:

- **implement experiment**;
- **evaluate evidence**;
- **promote to production**.

Those are separate decisions. A successful experiment does not automatically justify production
complexity.
