# Documentation authority order

This repository ships several documents that describe the same system. When two of them
disagree, this page decides which one is right, so a reader never has to guess and a
contributor knows which file to edit first.

## The order

Highest authority first. A lower document may add detail; it may never contradict a
higher one.

| Rank | Document | Owns | Read it when |
| --- | --- | --- | --- |
| 1 | [`SPEC.md`](../SPEC.md) | Locked decisions: the contract, the endpoints, the policy numbers, the profiles. | You need to know what the system is committed to. |
| 2 | [`ARCHITECTURE.md`](../ARCHITECTURE.md) | Ports, adapters, the kernel/vertical boundary, sequences. | You need to know how it is built. |
| 3 | [`COMPLIANCE.md`](../COMPLIANCE.md) | The principle-to-control map and its evidence pointers, plus the adopter-owned regulator crosswalk. | You need to know which control satisfies which principle. |
| 4 | [`README.md`](../README.md) | Orientation, quick start, the guided tour. | You are new here. |
| 5 | Everything else in [`docs/`](.) (runbook, ADOPTING, FAQs, DEMO) | Operational and adoption detail. | You are doing the thing. |

One document sits outside this ladder because it describes reality rather than intent:

- [`docs/practices-audit.md`](practices-audit.md) is the authority on this repo's
  common-base-practices verdicts. It is a projection of the code, so where it and any
  other document disagree, the code and the audit win together.

Per-system status for the wider catalog is NOT owned here: it lives in the maintainer's system
tracker, and the public view of it is the
[organization front page](https://github.com/portable-genai). This repo's documents link there
rather than restating it.

## Staleness is a bug, not a footnote

A shipped feature described as "forthcoming", "planned" or "not built" is a defect of the
same class as a wrong return value: it makes the higher-authority document untrue.

`tests/unit/test_release_docs.py` enforces the mechanical part of this: no document
above claims a feature is unbuilt when the code and the audit say it ships.

The judgement part is a review responsibility: when you change behavior, update the
highest-ranked document that describes it, then work down.

## Which file do I edit?

| The change is... | Edit |
| --- | --- |
| A new or changed commitment (endpoint, policy number, profile) | `SPEC.md` first, then the code |
| A new port, adapter or boundary | `ARCHITECTURE.md`, plus `CONTRIBUTING.md` if the touch list moves |
| A new control, or new evidence for one | `COMPLIANCE.md` (and the crosswalk appendix if a regulator cites it) |
| A practices-audit verdict | `docs/practices-audit.md`, then the catalog CSV row |
| Anything user-facing about running it | `README.md` / `docs/runbook.md` / `DEMO.md` |
