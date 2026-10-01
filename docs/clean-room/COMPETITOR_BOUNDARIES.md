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
