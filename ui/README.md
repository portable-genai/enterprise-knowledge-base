# `enterprise-knowledge-base` : Demo UI

A demo console for `enterprise-knowledge-base`, the ACL-aware governed RAG over the bank corpus. It is a thin
presentation layer over the `enterprise-knowledge-base` FastAPI backend: it renders ACL-filtered passages, grounded
answers and the governance signals, and never bypasses the guardrail or the maker-checker
gate.

Built with **Next.js (App Router) + TypeScript + Tailwind**. Dependencies are kept minimal:
`next`, `react`, `react-dom`, `tailwindcss`, `postcss`, `autoprefixer`, `typescript`, and
the `@types` packages, nothing else.

## What it shows

- **Query panel** (`ChatPanel`): pick search vs grounded answer, enter the query and the
  caller&rsquo;s ACL principals (comma-separated). Access scoping is explicit so the
  ACL-filtering behaviour is visible: change the principals and watch which documents
  appear or disappear.
- **Grounded answer** (`AnswerView`): the synthesised answer with a confidence meter, the
  human-review banner when `requires_human_review` is set (P-06), caveats, and page-level
  citation cards.
- **Passages** (`SearchView`): each ACL-admitted passage with the tags that admitted it.
- **Corpus freshness** (`CorpusStatus`): a live roll-up of the freshness + residency ledger.

## Run it (source only)

This UI is delivered as source and is part of `make check`: TypeScript, node tests, a production
Next build, hydration proof and the high-severity dependency audit all run in the offline gate.

```bash
cd ui
cp .env.local.example .env.local   # point NEXT_PUBLIC_KB_API_URL at the backend
npm install
npm run dev                        # http://localhost:3000
```

UI environment values are three-state. An absent `NEXT_PUBLIC_KB_API_URL` uses the documented
loopback development API, while an explicitly empty value selects same-origin. An absent
`NEXT_PUBLIC_FRAME_ANCESTORS` uses `'self'`, while an explicitly empty value refuses the build
because it would produce an invalid CSP directive. `tests/three-state-env-reads.test.mjs` scans
every shipped JavaScript and TypeScript source and is mutation-proved to fail on a new two-state
read.
The managed image sets the empty value in both build and runtime stages. This keeps middleware's
response CSP at `connect-src 'self'`; leaving it unset at runtime would reintroduce the local
`http://localhost:8082` development origin even though the compiled browser client is same-origin.

The backend must be running (`enterprise-knowledge-base serve`, default `:8082`) with CORS allowing
`localhost:3000` (already configured in `api/app.py`).

## Contract

Every type in `lib/types.ts` mirrors a frozen dataclass in
`src/enterprise_kb/domain/models.py`, serialised by `domain/serialization.to_jsonable`
(snake_case field names, enums as their `.value` strings). `lib/api.ts` is the typed client
for the SPEC §6 endpoints.
