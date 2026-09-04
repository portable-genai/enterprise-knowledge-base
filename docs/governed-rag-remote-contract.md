# Governed-RAG remote contract

`enterprise-knowledge-base` owns the sibling-service contract for ACL-aware search and grounded answers. Consumers
must discover it at `/.well-known/governed-rag-contract` and use the schemas in `/openapi.json`.
The contract identifier is `hrz2.governed-rag`; its current version is `1.0`.

## Operations

| Operation | Method and path | Request | Response |
|---|---|---|---|
| Search | `POST /v1/search` | `SearchRequest` | `SearchResponse` |
| Answer | `POST /v1/answer` | `AnswerRequest` | `AnswerResponse` |

The Python constants in `enterprise_kb.api.contract`, the FastAPI routes and the discovery
document use the same identifiers. Contract tests compare the generated OpenAPI components to
the security invariants below, so a route or schema cannot drift only in prose.

## Identity and authorization invariants

- Requests never carry `actor` or `tenant`. Both come from the server-verified `Principal`.
- `acl_principals` is optional and can only narrow the verified principal's entitlements.
- The managed principal-to-tag query includes the verified tenant in the SQL predicate.
- Search returns only tenant-and-ACL-admitted passages.
- Answers carry citations and human-review state. Missing grounding refuses or returns an
  explicitly escalated envelope; it never becomes an uncited successful answer.
- Governed routes require both the end-user identity ring and the service-caller ring described
  in `docs/embedding-and-identity.md`.

## Compatibility

Additive optional request fields and additive response fields may retain major version `1`.
Removing or renaming an operation or field, weakening an identity invariant, changing the
meaning of ACL narrowing, or making a required evidence field optional requires a new major
version and a parallel migration window. Consumers must not define private variants of these
operations; proposed changes belong in this repository first.
