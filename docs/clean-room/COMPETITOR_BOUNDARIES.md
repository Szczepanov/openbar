# Clean-room competitor boundaries

OpenBar can learn from publicly observable user needs and documented product capabilities in the market. It must not copy proprietary implementations.

## Allowed

- independently implementing generic concepts such as plate tracking, calibration, kinematics, graphs, rep segmentation, or video comparison;
- reviewing public documentation to understand user-visible requirements;
- reading published academic literature and appropriately licensed open-source projects;
- benchmarking OpenBar against independently created ground truth or legitimately obtained products.

## Not allowed

- decompiling, extracting, or reverse engineering proprietary application code/models;
- copying proprietary source code, prompts, model weights, datasets, assets, screenshots, iconography, product text, or distinctive UI layouts;
- submitting code based on leaked/non-public implementation details;
- bypassing technical protections to inspect proprietary internals;
- assuming that a publicly visible feature grants rights to underlying implementation or assets.

## Contribution requirement

A contributor who references external code/data/model material in a PR should identify the source and licence so compatibility can be checked.

When uncertain, implement from first principles or omit the material until reviewed.


## Public repository licence rule

Public visibility is not the same as permission to reuse.

For a public repository with no explicit licence or other documented permission:

- source may be read to understand publicly observable behaviour and to inform independent research questions;
- do not copy, adapt, redistribute, vendor, translate, or port its code;
- do not import model weights, datasets, media, generated assets, or trained checkpoints;
- do not assume the licence of a framework (for example, an ML framework) also licenses a third party's trained model or dataset;
- record the repository and the absence of a licence in research/provenance notes when it materially influenced an experiment.

If later permission or a licence is obtained, record the exact scope and version/commit before re-evaluating reuse.

## Research-reference documentation

When an external project materially motivates an OpenBar experiment, document:

- repository/source and stable commit/tag when relevant;
- observed idea or behaviour;
- licence/permission status;
- whether the influence is requirement-level, methodological, or implementation-level;
- what OpenBar will implement independently;
- any model/data/native dependency implications.

Research notes do not override the dependency policy, M0 scope fence, or accepted ADRs.
