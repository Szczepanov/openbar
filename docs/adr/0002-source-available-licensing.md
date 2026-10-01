# ADR-0002: Start source-available under PolyForm Shield 1.0.0

- Status: Accepted for project bootstrap; professional legal review required before product launch
- Date: 2026-10-01

## Context

The project owner wants the source to be inspectable and modifiable and may distribute a useful free official app, while retaining the option for future paid functionality. A central requirement is to discourage a third party from taking the repository, rebranding it, and offering a competing product.

OSI-approved open-source licences permit commercial use, so that requirement is not compatible with describing the project as conventional open source.

## Decision

Start the repository under the unmodified PolyForm Shield License 1.0.0 and describe OpenBar as **source-available**.

Keep product notices in `NOTICE`.

Do not claim that the licence alone protects the business. Before public product launch, obtain professional review covering:

- selected licence and notices;
- ownership entity/licensor identity;
- trademark strategy;
- contributor agreement;
- App Store/Play Store terms;
- privacy/data obligations;
- third-party model/data licences;
- potential dual/commercial licensing.

## Product/IP layers

Potential future separation:

- local measurement engine/application: source-available;
- official app distribution and trademarks: controlled by project owner;
- optional hosted services: may remain private;
- premium models/datasets: licensed separately if appropriate.

## Consequences

The repository is not OSI open source and should not be advertised as such.

The owner can later choose a more permissive licence for future versions, but rights already granted for published versions cannot simply be retroactively withdrawn.

## Alternatives considered

- MIT/Apache-2.0: too permissive for the stated anti-cloning goal.
- GPL/AGPL: strong copyleft but still permits commercial competing forks.
- PolyForm Noncommercial: broader commercial-use restriction than the stated goal.
