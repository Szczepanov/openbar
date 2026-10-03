# Subagent Routing & Delegation (OpenBar)

Subagents are an optimization tool, not the default execution path. This document governs when and how subagents are spawned, budgeted, and managed in OpenBar. Authorization to delegate is **not** a mandate to delegate.

## Objective & Priority

Optimize in this strict order:
1. **Measurement correctness and contract integrity** (OpenBar invariants outrank speed or features);
2. **Total expected resource cost** (model quota, context bloat, duplicate repository reads, repair cycles);
3. **Latency** (parallelism only for genuinely independent tasks);
4. **Agent economy** (smallest number of agents that materially improves the result).

A cheaper or smaller agent is not cheaper if it duplicates discovery, copies massive context, or causes validation repair loops.

---

## Parent Responsibility and Critical Path

The parent agent owns:
- System architecture, decomposition, and milestone scope (M0 boundaries);
- Ambiguous domain or measurement decisions;
- The immediate critical path;
- Integration, conflict resolution, and final verification.

**Keep the next blocking step local.** Delegate only bounded, non-overlapping sidecar work that can proceed while the parent continues useful progress.

### When to Delegate
Delegate only when the work is:
- Self-contained and independently specifiable;
- Non-blocking to the parent's next action;
- Free from repeating discovery already performed by the parent;
- Easy to verify from a bounded diff or concise report;
- Assigned a clear, disjoint write boundary (if modifying code).

### When to Keep Work Local
Keep work local when it is:
- On the immediate critical path or tightly coupled;
- Already understood by the parent;
- Likely to require the same file reads as the parent;
- Likely to trigger cross-agent coordination overhead;
- Trivial or mechanical.

---

## Concurrency and Usage Budget

- **Default:** **0–1 active subagent**.
- **2 active subagents:** Allowed only when workstreams are clearly independent with disjoint file sets.
- **3 active subagents:** Exceptional; permitted only when concerns/write sets are completely disjoint and the gain clearly justifies the resource cost.
- **Never fill concurrency slots automatically.**

Before spawning a subagent, verify:
1. Does an existing agent already cover the question?
2. Can the task be a follow-up to an existing agent?
3. Would the new agent reread the same repository files?
4. Is the output strictly needed now?
5. Is the quality or latency gain worth an independent model run?

Close or terminate idle agents promptly. Interrupt or steer drifting agents rather than letting wasteful runs continue.

---

## Context Inheritance and Prompts

Minimize inherited parent context:
- Avoid forwarding the entire session history; provide task-local essentials.
- Every delegated prompt must explicitly state:
  1. **Objective and Acceptance Criteria**;
  2. **Owned files/modules** or write boundary;
  3. **Known facts and cited evidence** (never make a subagent rediscover what the parent already knows);
  4. **OpenBar Invariants**: raw observations preserved, no fabricated coordinates across gaps, authoritative timestamps, determinism (`BTreeMap`), finite-number checks, pixel-centre coordinates (ADR-0007);
  5. **Required verification commands**.

---

## Available Subagent Roles in OpenBar

Located in `.claude/agents/`, `.codex/agents/`, and `.gemini/agents/`:

| Role | Purpose | Write Allowed? | Typical Use Case |
|---|---|:---:|---|
| **`code-reviewer`** / **`measurement-reviewer`** | Diff-first domain invariant & code review | No (Read-only) | Phase 7.5 review before opening a PR; reviewing diffs touching domain logic or schemas |
| **`planner`** | Implementation planning adhering to `docs/plans/README.md` | No (Read-only) | Scoping complex features, new tracker algorithms, or refactors |
| **`code-validator`** | Runs compiler, lints, tests, and schema checks independently | No (Read-only) | Independent reproduction of verification gates (`cargo test`, `schema_check.py`) |
| **`quick-implementer`** | Small, mechanical edits on 1–2 files | Yes | Well-specified bug fixes, adding a test case, updating a schema entry |
| **`implementer`** | Substantial, multi-file implementation slice | Yes | Self-contained work package with disjoint write ownership |
| **`code-explorer`** | Multi-file codebase scouting | No (Read-only) | Broad discovery when vocabulary is unknown; adheres to `semantic-code-discovery` |

---

## Delegation Guidelines by Phase

### 1. Repository Discovery
- Primary agent performs initial routing and immediate blocking inspection locally.
- Use `code-explorer` only when discovery spans many files or would dump large raw evidence into the parent context.
- Require decision-ready summaries ($\le 300$ words) citing exact symbols and files, not narrative log dumps.

### 2. Implementation
- Do **not** delegate implementation by default. The primary agent implements directly for critical-path or already understood changes.
- If delegating in parallel, write sets **must be disjoint**. Never parallelize writes to shared schemas (`validation/schema/`), central configuration, or lockfiles.

### 3. Validation
- The parent or implementer runs cheap, targeted checks directly (`cargo test -p <crate> <module>::`).
- Use `code-validator` when verification is substantial, benefits from clean-room independence, or runs in parallel with parent documentation updates.
- Send implementation test failures back to the **same implementer** so context is retained. Do not exceed 2 repair cycles before evaluating architectural root causes.

### 4. Independent Review
- Use `code-reviewer` / `measurement-reviewer` before PR creation for non-trivial code changes.
- Supply the reviewer with the branch diff (`git diff origin/main...HEAD`), acceptance criteria, and known risks. Do not ask the reviewer to repeat broad discovery.
