# Licensing and IP strategy

This document is an engineering/product decision record, not legal advice.

## Current repository licence

OpenBar starts under the **PolyForm Shield License 1.0.0**. The standard text is in `LICENSE.md`; project-specific notices are in `NOTICE`.

PolyForm Shield is source-available and restricts use for competing products. It is not an OSI-approved open-source licence and the project should use accurate terminology.

## Why this starting point

The intended combination is:

- public/inspectable source;
- community experimentation and modification;
- potential free official App Store/Play Store distribution;
- optional paid features later;
- avoiding an easy "fork, rebrand, sell a substitute" path.

Conventional permissive and copyleft open-source licences do not prohibit commercial competing forks.

## Before public product launch

Obtain professional advice on at least:

1. correct licensor/ownership entity and copyright notices;
2. whether PolyForm Shield and the selected notices fit the intended business;
3. contributor licence agreement / copyright grants;
4. trademark/name/logo registration and usage policy;
5. dual/commercial licensing if desired;
6. privacy and biometric/video/data obligations;
7. App Store and Google Play distribution/payment rules;
8. third-party Rust/Python/Flutter dependency licences;
9. model-weight and training-dataset licences.

## Layered protection

The licence should not be the only business protection.

Potential layers:

- copyright/licence for repository code;
- trademark for official OpenBar identity;
- official store listings/distribution;
- private hosted services where appropriate;
- separate licences for premium models/datasets;
- user trust, validation data, and product quality.

## Relicensing direction

Starting conservatively preserves options for future versions. Before accepting external contributions, establish contributor terms broad enough to preserve any intended dual/commercial licensing flexibility.

Do not add ad-hoc exceptions to the standard PolyForm licence text without legal review; use separate notices or a separately drafted commercial agreement instead.
