# Features FAQ

## What does `enterprise-knowledge-base` own?

It owns governed corpus ingestion, ACL-aware retrieval, citations, grounded answer
orchestration and freshness metadata. It is not the shared safety or audit platform.

## Which sibling systems own adjacent controls?

`agent-guardrail-gateway` owns shared safety, `agent-registry` agent registration, `model-quality-gate` quality promotion, `agent-observability` cross-service
audit and `human-review-console` human review. `enterprise-knowledge-base` integrates through explicit ports.

## Does an empty retrieval invent an answer?

No, and since the B2 closure it does not return a soft non-answer either: the service
RAISES `RetrievalEmptyError`, which the API surfaces as a structured HTTP 422 refusal
flagged for review. The escalated audit record is written before the refusal, so a refused
request stays on the WORM trail. A deployment that needs the older caveated envelope sets
`policy.answer.empty_retrieval_raises: false`.
