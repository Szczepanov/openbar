# SAM 3 licence review (#233)

Date: 2026-10-17. Reviewer: an agent, from the licence text; this is not legal advice. Decision: the
owner (below). Model: `facebook/sam3` on Hugging Face (gated), used in `research/plate-detection/`.
Licence: Meta's **SAM License**, last updated 19 November 2025
(<https://github.com/facebookresearch/sam3/blob/main/LICENSE>).

## What the licence says

- **Grant:** a non-exclusive, worldwide, non-transferable, royalty-free licence to use, reproduce,
  distribute, copy, modify and create derivative works of the "SAM Materials" (weights, code,
  documentation). Commercial use is neither expressly allowed nor expressly forbidden.
- **Redistribution:** anyone who receives the SAM Materials or a derivative must also receive the
  agreement, and redistribution happens under its terms. The weights therefore cannot be relicensed
  under MIT.
- **Attribution:** published research results must acknowledge the use of the SAM Materials.
- **Restrictions:** compliance with applicable law, including trade-control and privacy law; no
  reverse engineering of the materials; no military, warfare, nuclear, espionage or weapons end uses.
- **Outputs:** masks, boxes and scores the model produces are not restricted beyond the warranty
  disclaimer. The text does not address models trained on those outputs.
- **Other terms:** provided as is, with no warranty. There is no patent grant. The licence terminates
  for breach, or if the user sues Meta over IP; on termination the materials must be deleted. You must
  indemnify Meta against third-party claims. California law applies. Meta may change the terms, and
  continued use means accepting the change.

## Fit with OpenBar

OpenBar is MIT (ADR-0011). The SAM License is neither non-commercial nor copyleft for OpenBar's own
code, so it does not hit the exclusions in ADR-0011 or `LICENSING_STRATEGY.md`. But the weights stay
under Meta's terms, so the MIT code and the SAM 3 weights must stay separate:

| Use | Status |
| --- | --- |
| Research runs and development evaluation on the owner's machine | Allowed (already approved in #233) |
| Owner's personal home-PC pipeline (process between sets, confirm on the session page) | Allowed |
| Committing SAM 3 weights or vendored SAM 3 code to this repository | Not allowed: weights stay downloaded under `validation/private/models/`, git-ignored, with SHA-256 in run provenance |
| Code in this repository that loads SAM 3 through `transformers` (Apache-2.0) | Allowed; the user downloads the weights and accepts Meta's terms themselves |
| Training a small detector on SAM 3 masks or boxes (Phase B) | Allowed by the text, which does not restrict outputs; the trained detector should be Apache-2.0 or MIT and should not contain SAM 3 weights |
| Shipping SAM 3 weights inside a distributed app | Needs a new review: ship the SAM License with them, show it in third-party notices, and check the end-use and trade-control terms for the distribution channel |

## Owner decision

2026-10-17, in session: the owner does not plan to sell OpenBar and accepts any free licence for its
own use. SAM 3 is accepted for research and for the owner's personal pipeline under the terms above,
and is the lead plate-selection candidate for the next frozen run (#233). This does not record a
PROCEED for production automatic selection (#178's successor) or authorise M1 work. Measurement
rules are unchanged: SAM 3 output is machine-origin, a human confirms the seed, and nothing enters
the canonical `Analysis` without the existing contracts.

Re-review if any of these happen: the weights are distributed (app or download bundle), OpenBar is
sold or offered as a paid service, Meta changes the licence, or SAM 3 output starts feeding a
published dataset.
