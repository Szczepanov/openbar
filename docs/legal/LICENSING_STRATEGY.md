# Licensing and IP strategy

This document is an engineering/product decision record, not legal advice.

## Current repository licence

OpenBar is licensed under the **MIT License**. The text is in `LICENSE`. ADR-0011 records the
decision and supersedes the earlier PolyForm Shield start (ADR-0002).

## Why MIT

- The source is public either way. A licence restriction that the owner would never detect or
  enforce adds friction without protection.
- MIT is widely understood, compatible with the Rust and Python dependency graph, and lets
  OpenBar be called open source.
- Outside contributions can be accepted without a CLA.

## What still needs care

The licence covers OpenBar's own code and repository-generated data. It does not change the
licence of anything OpenBar uses:

1. **Dependencies:** the `deny.toml` allow-list is the gate for Rust crates. Python tooling stays
   stdlib-only, except for research environments.
2. **FFmpeg:** it is invoked as a separate program and not linked (ADR-0006). Shipping FFmpeg
   binaries with an app needs its own LGPL/GPL review.
3. **Models and datasets:** research checkpoints are downloaded, not committed. Non-commercial
   (for example CC-BY-NC) or copyleft (for example AGPL-3.0) models must not be merged into
   OpenBar or shipped with it.
4. **Media:** the fixture manifest records rights for each video. Only fixtures whose
   redistribution is documented are committed.

## Before a public app release

Check, with advice if needed:

1. trademark and name use for "OpenBar" and any logo (the licence does not cover them);
2. privacy and video/biometric data obligations;
3. App Store and Google Play distribution and payment rules;
4. third-party notices that the app must display (dependencies, FFmpeg if bundled, models).

## Layered protection

Without a restrictive licence, the remaining advantages of the official project are:

- trademark and the official OpenBar identity;
- official store listings and distribution;
- private hosted services, if any are ever built;
- separately licensed premium models or datasets, if any;
- validation evidence, user trust and product quality.
