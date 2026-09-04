# Security FAQ

## Can callers choose their actor or ACL?

No. Identity is resolved server-side and client ACL input can only narrow verified
entitlements. Tenant and all-of ACL filtering run in the domain.

## Where do shared safety and human review belong?

`agent-guardrail-gateway` owns shared guardrails and redaction. `human-review-console` owns durable human decisions. `enterprise-knowledge-base` applies
its local policy seam and preserves the evidence used by those systems.

## Is local identity suitable for production?

No. It contains seeded fictional personas for offline demo and test. Managed deployment
verifies IAP assertions; on-prem requires an institution-owned IdP adapter.
