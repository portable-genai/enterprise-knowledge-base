# Demo guide : Hrz2 Enterprise Knowledge Base

Step-by-step scripts for demoing Hrz2 two ways:

- **Demo A : ACL-aware governed RAG, end to end** (the headline flow): the same seeded
  corpus seen by three callers : a retail RM gets a cited answer queued for standard
  review; a risk officer asks about a restricted source and the gate escalates to enhanced
  review; an unknown caller resolves to no ACL tags, sees nothing, and is refused rather
  than given an uncited answer (fail-closed). Plus a live ingest that
  redacts an email and an NRIC before indexing, and citations that resolve to the layout
  block (`p.4 block p4#b1`) a reviewer would open. Runs **fully offline** (no cloud, no API
  key, no emulator).
- **Demo B : the same surface on the managed GCP stack**: the REST endpoints and the
  Next.js console talking to a FastAPI service backed by private AlloyDB FTS, the same
  portable parser, redacted regional GCS, Gemini, Model Armor and DLP in `asia-southeast1`.

> The synthetic corpus is **fictional** (invented policies, runbooks and standards). Do
> not run against the live bank corpus without your own legal, security and model-risk
> sign-off.

---

## 0. Prerequisites

| Need | Demo A (local) | Demo B (GCP) | Notes |
|------|:--:|:--:|-------|
| `git` | yes | yes | clone the repo |
| **Python 3.12+** | yes | yes | the package pins `>=3.12` |
| Node.js 18+ & npm | for the UI / Playwright | for the UI | only if you show the browser console |
| **Playwright** (`pip install playwright` + `playwright install chromium`) | for the guided walkthrough | : | Demo A's presenter walkthrough only |
| A GCP project + `gcloud` | : | yes | billing enabled; `asia-southeast1` available |
| Terraform | : | yes | provisions AlloyDB, regional buckets/PSC, Cloud Run, IAP and CMEK |

Install/setup references (read these once):

- Local install & profiles : [README §4.1 `local`](README.md#41-local-profile-working-offline-stack-no-gcp-no-emulator)
- GCP install & deploy : [README §4.3 `gcp`](README.md#43-gcp-profile-real-managed-stack-in-asia-southeast1) and [`docs/runbook.md`](docs/runbook.md#deploy--rollback)
- Running the surfaces (CLI / API / UI) : [README §5](README.md#5-running-the-surfaces)
- The demo scripts : [`scripts/README.md`](scripts/README.md)
- The UI console : [`ui/README.md`](ui/README.md)
- ACL-aware governed access (the headline control) : [README §6](README.md#6-acl-aware-governed-access)

---

## 1. Common setup (both demos)

```bash
git clone https://github.com/portable-genai/enterprise-knowledge-base.git
cd enterprise-knowledge-base

python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # core + dev tooling (NO google-cloud-* packages)

# Sanity-check the offline stack before presenting:
export KB_PROFILE=local
ruff check src tests
pytest -m 'not integration' -q   # all local, no cloud
```

See [README §4.1](README.md#41-local-profile-working-offline-stack-no-gcp-no-emulator) for details.

---

## 2. Demo A : ACL-aware governed RAG (local, offline)

The flow uses the in-process `local` stack (SQLite FTS5 retrieval + a deterministic LLM),
so it needs **no Google Cloud and no API key** : ideal for a laptop demo. Four ways to
present it, in order of polish.

### 2.1 Guided, presenter-controlled walkthrough (recommended)

A real browser opens; the script narrates each step and **waits for you to press Enter**
before performing it, so you control the pace. (One-time: `pip install playwright &&
playwright install chromium`.)

```bash
# Terminal 1 : the live demo server (http://localhost:8092)
source .venv/bin/activate
KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo_server.py

# Terminal 2 : the guided walkthrough (a Chrome window opens)
source .venv/bin/activate
python scripts/kb_demo_playwright.py
```

You'll step through, pressing Enter each time:

1. **Retail RM** : `user:jane@bank.test` (tags `dept:retail` + `classification:internal`)
   asks what due diligence is required before onboarding a cloud provider. The query is
   PII-redacted at the boundary, the corpus is ACL-filtered to her tags, and the answer is
   cited to source pages. Maker-checker is a floor, so it is queued for **standard
   review**: no hard signal raised the bar.
2. **Risk officer** : `group:kb-approver` asks where restricted records must be stored. The same
   corpus surfaces a `classification:restricted` source she is entitled to see, so the gate
   escalates to **ENHANCED review** (P-06) and records the reason : the agent proposes, a
   checker disposes.
3. **Unentitled principal** : `user:nobody@bank.test` asks the same cloud-onboarding
   question; this domain-level ACL scope resolves to **no tags**, so the filter drops
   everything (**fail-closed**, P-09) and the answer is **REFUSED** rather than softened
   into an uncited reply (B2).
4. **ACL visibility matrix** : one corpus, three callers, side by side, plus the live
   ingest that redacted an email and an NRIC before indexing (P-04).

**What to point at on screen:** the ACL tag chips on each admitted passage (the proof of
*why* it was visible), the page-level citation chips on the grounded answer, the
human-review banner that appears only for the restricted source, the fail-closed empty
state for the unentitled principal, and the per-caller matrix. Full options (`SLOWMO_MS`,
`HEADLESS`, `CHROME_PATH`, `DEMO_URL`) are in [`scripts/README.md`](scripts/README.md).

### 2.2 Manual, click-through (no Playwright)

Run only the server and drive it yourself in any browser:

```bash
KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo_server.py     # http://localhost:8092
```

Open `http://localhost:8092` and click **Next ▶** to advance through the personas,
**Restart** to reset. Same four steps as above.

You can also click through the **real console** instead of the demo server:

```bash
make run-api PROFILE=local      # FastAPI on :8082
make run-ui                     # Next.js console on :3000
```

In the console's query panel, run the same query as `user:jane@bank.test`, then change the
principals to `group:kb-approver` and to `user:nobody@bank.test` and watch which documents appear
or disappear and when the human-review banner shows.

### 2.3 Static artifacts (slides / screenshots)

Generate the audit-first pages and JSON without a browser:

```bash
KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo.py kb_demo.json        # prints the per-persona summary
KB_PROFILE=local PYTHONPATH=src python scripts/render_kb_ui.py kb_demo.json ./out
# -> ./out/kb-persona-retail.html, kb-persona-risk.html, kb-persona-unknown.html, kb-acl-matrix.html
```

Or simply `make demo` (writes `kb_demo.json` and renders the same pages under `./out/`).

### 2.4 One-shot answer via the CLI (quick variant)

If you only want to show a single cited, ACL-filtered answer (not the full walkthrough):

```bash
export KB_PROFILE=local

# Auto-cleared, cited answer for the retail RM:
enterprise-knowledge-base answer \
  "What due diligence is required before onboarding a cloud provider?" \
  --principals "user:jane@bank.test" --tenant "demo-bank"

# The risk officer hits the human-review gate on a restricted source:
enterprise-knowledge-base answer "Where must records classified restricted be stored?" \
  -p "group:kb-approver" --tenant "demo-bank"

# An unentitled principal is fail-closed at the domain ACL seam:
enterprise-knowledge-base search "cloud onboarding due diligence" \
  -p "user:nobody@bank.test" --tenant "demo-bank"
```

Every command names `--tenant`. Omitting it is not a wildcard: an unnamed tenant reads the
shared corpus and nothing else, so a run without it would show the ACL seam over the wrong
evidence and read as a weaker demo than it is.

---

## 3. Demo B : the same surface on the managed GCP stack

Shows the identical governed-RAG surface against **real managed services** in
`asia-southeast1`. This is a release of the private Cloud Run UI/API and refresh job, not a
developer process running the `gcp` profile on localhost. Follow
[`docs/runbook.md`](docs/runbook.md#deploy--rollback) for the authoritative operations path.

### 3.1 Review the release inputs

Configure the protected `managed-bootstrap` and `managed-release` GitHub environments described
in [`infra/terraform/README.md`](infra/terraform/README.md#two-phase-managed-demo-release). The
reviewed tfvars JSON must include the presenter as an exact `user:<email>` IAP accessor, the
Singapore Gemini 3.5 Flash single-zone Provisioned Throughput confirmation, the existing public
Cloud DNS zone and the exact project-derived registry/ACL object URIs. No database password,
OAuth secret or service-account key is accepted.

### 3.2 Run the two approval phases

Dispatch **Managed demo release** with a lowercase release id, the exact IAP demo user email and
the fictional tenant `bank.example`.

- Phase 1 enables the required APIs, creates regional CMEK and its Artifact Registry binding,
  builds the API/UI/refresh images, pushes them to the regional repository and retains their
  immutable digests. It cannot deploy an application resource.
- Phase 2 starts only after the `managed-release` environment is approved. It applies the full
  regional stack and DNS/IAP edge, runs the passwordless 000/001 migrations from the VPC runner,
  publishes the reviewed fictional raw source and separate registry/ACL authority with their
  least-privilege publisher identities, and waits for the first refresh job.

The retained digest, migration and artifact checksum evidence ties the browser demonstration to
the reviewed release. This workflow is provided as code but is not executed by the offline gate.

### 3.3 Open the managed browser journey

Open the `managed_api_url` printed in the workflow summary and authenticate through IAP as the
exact demo user. Keep the optional ACL narrowing field empty; the server derives identity from
the verified IAP assertion and resolves its tags from the published ACL authority. Ask:

> What due diligence is required before onboarding a cloud provider?

**What to highlight:** the local and managed browser screens exercise the same domain and HTTP
contract, but their outer adapters differ. Local uses seeded personas and SQLite; managed uses
IAP, synchronized tenant/tag bindings, the regional DLP/Model Armor private endpoints, AlloyDB
FTS and Cloud Logging. A client `acl_principals` value can only **narrow**, never widen, the
verified scope. Every passage and claim carries a **citation** resolved to the layout block and bounding
box a reviewer opens (`"anchor": "p2#b1"` plus `"bbox"` in the JSON, `p.2 block p2#b1` in
the rendered pages), falling back to the page when a claim clears no block; PII is redacted before
any model / index / audit call (P-04); low-confidence or restricted-ACL answers are marked
human-review (maker-checker, P-06); everything stays in `asia-southeast1` with CMEK + VPC-SC
([README §8](README.md#8-security--residency-posture)).

---

## 4. Talking points

- **Access lives in the domain, not the adapter.** The retrieval adapter surfaces each
  passage's ACL tags; `filter_by_allowed_tags` in the domain admits or drops them, and an
  untagged passage or a tagless caller is dropped (fail-closed, P-09). No adapter decides
  access : that is what makes the GCP stack swappable for an on-prem one.
- **Grounded, never beyond the retrieved set.** The answer cites only the documents the
  caller was permitted to see; a self-critique pass adjusts confidence; nothing is
  invented past the passages.
- **The citation points at the paragraph, not at the document.** Parsing is layout-aware,
  so a document is a set of blocks with boxes, and the block a claim came from is chosen by
  deterministic code scoring the claim against the stored blocks : the model never returns
  an anchor, a page or a box. Show it by pointing at `anchor` and `bbox` in the answer JSON
  and noting that a claim which matches no block above the configured floor keeps its
  page-level citation rather than getting a confident wrong pointer. The eval gate scores
  this at anchor level against hand-declared golden anchors, so a resolver that drifts one
  block turns the build red.
- **Redact before everything.** The query is redacted before it reaches retrieval, and a
  document is redacted before it is parsed or indexed (the live ingest shows an email and
  an NRIC masked, P-04).
- **The maker-checker gate is a floor, not a switch.** Every synthesised answer is queued
  for a checker (P-06); low confidence or a sensitive-classification grounding source only
  raises the level to enhanced : the retail and risk-officer steps show both levels.
- **Same code, three surfaces.** CLI, REST and the console all call one
  `KnowledgeBaseService`; the demo drives the same service the tests exercise.

---

## 5. Troubleshooting & cleanup

| Symptom | Fix |
|---------|-----|
| `python3.12: command not found` | Install Python 3.12+; the package pins `>=3.12`. (For `make demo` you can also pass `PYTHON=python` if your venv exposes only `python`.) |
| `ModuleNotFoundError: enterprise_kb` running a script | Export `PYTHONPATH=src` (the scripts import the package directly). |
| Playwright: "executable doesn't exist" | `playwright install chromium`, or set `CHROME_PATH=/path/to/chrome`. |
| No display for the headed walkthrough | Use §2.2 (manual browser) on a machine with a display, or `HEADLESS=1 DEMO_AUTO=1 python scripts/kb_demo_playwright.py` to self-run. |
| "Cannot reach the demo server" | Start §2.1 Terminal 1 first; or set `DEMO_URL` if you changed `--port`. |
| Port 8092 / 8082 in use | `python scripts/kb_demo_server.py --port 9000` (then `DEMO_URL=http://127.0.0.1:9000`); API port via `make run-api API_PORT=...`. |
| CLI exits with code 2 | You're on `KB_PROFILE=onprem` (fail-fast placeholders). Use `local` (Demo A) or `gcp` (Demo B). |
| Unentitled principal sees nothing | Expected in this domain-level ACL scenario. Use `user:jane@bank.test` or `group:kb-approver` for visible examples. |
| GCP deploy / region / VPC-SC errors | See [`docs/runbook.md`](docs/runbook.md). |

**Stop / clean up:** Ctrl-C the demo server and `make run-api`. `make clean` removes local
caches/artefacts; the demo writes `kb_demo.json` and `./out/` (both gitignored). The local
index lives at `~/.enterprise_kb/local.db` and can be deleted to reseed. For GCP, scale the
deployment to zero : the audit trail and corpus remain intact ([`docs/runbook.md`](docs/runbook.md#deploy--rollback)).
