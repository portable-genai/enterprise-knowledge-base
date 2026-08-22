# Features FAQ

## What does Hrz2 own?

It owns governed corpus ingestion, ACL-aware retrieval, citations, grounded answer
orchestration and freshness metadata. It is not the shared safety or audit platform.

## Which sibling systems own adjacent controls?

Hrz1 owns shared safety, Hrz3 agent registration, Hrz4 quality promotion, Hrz5 cross-service
audit and Hrz7 human review. Hrz2 integrates through explicit ports.

## Does an empty retrieval invent an answer?

No, and since the B2 closure it does not return a soft non-answer either: the service
RAISES `RetrievalEmptyError`, which the API surfaces as a structured HTTP 422 refusal
flagged for review. The escalated audit record is written before the refusal, so a refused
request stays on the WORM trail. A deployment that needs the older caveated envelope sets
`policy.answer.empty_retrieval_raises: false`.
