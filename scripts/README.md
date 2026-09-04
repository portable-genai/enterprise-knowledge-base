# Demo scripts : `enterprise-knowledge-base` governed-RAG (ACL-aware, cited, offline)

All scripts are SDK-free and run against the in-process `local` stack (SQLite FTS5 +
deterministic LLM : no Google Cloud, no API key, no emulator). They drive the *real*
`KnowledgeBaseService` / `IngestionService` over the built-in synthetic corpus, so what
you demo is the same code the tests and the CLI exercise. Run them from the repo root with
the domain package on the path:

```bash
export KB_PROFILE=local
export PYTHONPATH=src
```

> The presenter Playwright script remains optional. CI directly lints and executes the
> browserless demo self-test, portability proof and rename utility.

| Script | What it does |
|--------|--------------|
| `kb_demo.py` | Runs the governed-RAG flow over three ACL scopes (retail RM, risk approver, unentitled principal) against the seeded corpus, ingests one document live to show redact-before-index, prints a per-step summary and writes the audit-view JSON. |
| `render_kb_ui.py` | Renders that JSON into static audit-first HTML pages (one per persona + an ACL visibility matrix) for screenshots / slides. |
| `kb_demo_server.py` | A **live, click-through** server that runs the *real* pipeline and reveals it one persona per click, rendering the audit-first UI. Serves on **:8092** (distinct from the API's :8082). |
| `kb_demo_playwright.py` | A **presenter-controlled** Playwright walkthrough of the live server (or the real Next.js console): it narrates each step and waits for you to press Enter before clicking **Next ▶**. |
| `demo_selftest.py` | Runs the real demo, validates observed ACL/citation/review/audit outcomes, checks every rendered page and invokes the live-server self-test. |
| `demo_server_selftest.py` | Self-starts the browserless server and strictly walks all four live states plus restart through HTTP. |
| `portability_demo.py` | Bounded executable proof of profiles, identity, audit export/reload and exit behavior. |
| `rename_fork.py` | Dry-run-first mechanical rename for an institutional fork. |

## Static screenshots

```bash
python scripts/kb_demo.py kb_demo.json                 # prints the summary, writes the JSON
python scripts/render_kb_ui.py kb_demo.json ./out      # ./out/kb-persona-*.html, kb-acl-matrix.html
```

## Live, presenter-controlled demo

Two terminals:

```bash
# 1) the live demo server  (http://localhost:8092)
KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo_server.py

# 2) the guided walkthrough  (a real Chrome window opens)
pip install playwright && playwright install chromium      # one-time
python scripts/kb_demo_playwright.py
```

The walkthrough is **paced by you**: it prints what the next step will do, waits for you to
press **Enter**, then clicks **Next ▶** and spotlights the panel to look at. The four steps
are: retail RM (auto-cleared) -> risk officer (restricted source -> human-review gate) ->
unentitled principal (fail-closed, sees nothing) -> the ACL visibility matrix.

You can also just open `http://localhost:8092` and click **Next ▶** / **Restart** by hand :
the server holds the live result, so the buttons drive the same real flow.

To point the walkthrough at the **real Next.js console** instead of the demo server (drive
the live UI yourself between steps), set `DEMO_URL`:

```bash
make run-api PROFILE=local            # FastAPI on :8082
make run-ui                           # Next.js console on :3000
DEMO_URL=http://localhost:3000 python scripts/kb_demo_playwright.py
```

Useful environment overrides for `kb_demo_playwright.py`:

| Var | Default | Purpose |
|-----|---------|---------|
| `DEMO_URL` | `http://127.0.0.1:8092` | server base URL (set to `http://localhost:3000` for the live UI) |
| `HEADLESS=1` | off | run without a window (self-test / recording) |
| `DEMO_AUTO=1` | off | don't wait for Enter : advance automatically |
| `SLOWMO_MS` | `250` headed | per-action slow motion |
| `CHROME_PATH` | : | explicit Chromium/Chrome binary |
| `lock.py` | Compiles both lockfiles and puts the header back, because `uv pip compile` REPLACES the output file: it writes its own two-line provenance comment and destroys the `tag = commit` map the pin tests check against. `make lock` runs this rather than uv directly. |

## Make target

`make demo` runs the offline static path (writes `kb_demo.json` and renders the HTML pages
under `./out/`). It needs no browser and no Playwright.

`make demo-selftest` and `make portability-demo` run the CI-gated evidence without
persisting output in the repository.
