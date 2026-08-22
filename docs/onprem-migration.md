# On-prem migration (reversibility / exit)

Hrz2 demonstrates reversibility (P-02, P-12) as a property you can run, not a claim. Switching
`KB_PROFILE` from `gcp` to `onprem` rebinds every port to a placeholder adapter targeting an
on-premise platform (Google Distributed Cloud). The domain core and every service caller are
untouched: only the adapter bodies change.

## What "on-prem" means here

The `onprem` adapter family (`src/enterprise_kb/adapters/onprem/`) ships placeholders that:

- construct cleanly with a single `Settings` argument and **no external dependencies**, and
- structurally satisfy the same `Protocol` as the managed `gcp` adapter (proven by
  `tests/contract/test_port_parity.py`).

Most stubs raise `NotImplementedError("...migration target... domain unchanged")` from every
method, with two deliberate exceptions:

- **Tracer** is a no-op (tracing is non-essential to correctness): `span` returns a
  `nullcontext`, `record_token_usage` does nothing, so the pipeline runs with no
  observability backend wired up.
- **Grounding** returns benign defaults (`enabled = False`, `ground = []`) modelling a
  closed, air-gapped perimeter with no public-web egress.

The safety-critical stubs (guardrail, redaction, audit, access control, evaluation)
deliberately raise rather than fail open: an unimplemented redactor must never leak PII, an
unimplemented guardrail must never allow traffic, an unimplemented access-control resolver
must never silently grant or deny visibility.

## How to migrate a port

1. Pick the port (e.g. `RetrievalPort`) and its on-prem stub
   (`adapters/onprem/retrieval.py`).
2. Implement the method bodies against your on-premise platform (e.g. a sovereign vector
   store and document index). Keep the exact signature; return the same domain types.
3. Nothing else changes: `config/settings.yaml` already binds the `onprem` profile to your
   class, and the contract test already proves interface parity.

## What stays identical

- The domain pipelines (`KnowledgeBaseService`, `IngestionService`) and the ACL-filtering
  decision (`filter_by_allowed_tags`) run unchanged.
- The HTTP contract (SPEC §6), the CLI, and the agent surface are profile-agnostic.
- The eval gate runs on the `onprem` profile with no cloud credentials.

## Verifying parity

```bash
KB_PROFILE=onprem pytest tests/contract -q
```

This imports and constructs every on-prem adapter with no Google Cloud SDK installed,
asserts each satisfies its `@runtime_checkable` Protocol, and confirms each declares every
Protocol member. A green run is the proof that the managed stack and the on-prem target are
interface-identical.
