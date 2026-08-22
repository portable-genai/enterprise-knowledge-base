# Portability FAQ

## What does the executable proof establish?

`make portability-demo` checks the port map, deterministic isolated local runs, SDK-free
managed construction, identity replacement, fail-fast on-prem behavior, unknown-selector
rejection and hash-verified JSON Lines audit export/reload.

## What remains unproved?

It does not prove live managed services, completed on-prem adapters, managed-search corpus
migration, cross-tenant migration or Hrz5 delivery. Those require target-host evidence.

## Why is platform a hybrid profile?

Hrz2 delegates guardrail/redaction to Hrz1, registry to Hrz3 and audit to Hrz5. Ports with
no sibling owner continue to use their managed Hrz2 adapter. Unknown profiles never receive
that fallback.
