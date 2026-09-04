# Contributing to `enterprise-knowledge-base`

Thanks for your interest. This is a public engineering-portfolio reference repo; the bar is
that every change keeps the offline gate green and respects the hexagonal boundaries.

## Setup

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"     # NO Google Cloud SDK : local/test profile
```

The profile for development and CI is `local`, a WORKING SDK-free offline stack that needs no
Google Cloud credentials. **Set `KB_PROFILE=local` deliberately** (the Makefile does it for
you). An unset `KB_PROFILE` still binds the SDK-free adapters, but it is treated as "nobody
chose", so it gets none of the `local` relaxations: no localhost CORS fallback, HSTS on, the
seeded dev personas refused, and every service-to-service route answering 401. The `onprem`
profile is the separate fail-fast migration placeholder (`KB_PROFILE=onprem`), exercised to
prove the portability boundary, not to develop against.

## The gate (must be green before you push)

```bash
ruff check src tests            # lint
ruff format --check src tests   # formatting
pytest -m 'not integration' -q  # unit + contract
mypy src                        # type-check (best-effort)
python eval/run_eval.py         # eval gate (exit 0)
make demo-selftest              # observed domain, audit and rendered evidence
make portability-demo           # bounded profile, identity, audit and exit proof
```

`ruff check`, `ruff format --check`, and `pytest -m 'not integration'` passing are
mandatory. The eval gate (`acl_correctness >= 0.99`) must pass too.

## Architecture rules (hexagon)

- **The domain is pure.** No `google-cloud-*`, ADK, FastAPI, httpx, or pydantic imports in
  `src/enterprise_kb/domain/`. Frozen dataclasses, enums, pure services that take explicit
  ports.
- **The ACL decision lives in the domain.** Never let a retrieval adapter decide what a
  caller may see; adapters surface tags, `filter_by_allowed_tags` decides (P-09).
- **GCP imports are lazy.** Every `google-cloud-*` / `genai` / `adk` import in a `gcp`
  adapter is inside a method or under `TYPE_CHECKING`, never at module top level.
- **One construction convention.** Every adapter is `Adapter(settings: Settings)`.
- **Cite and audit.** Every consequential output carries page-level citations and writes a
  WORM audit event.

## Tests

- Add real unit tests driven by the in-memory fakes in `tests/conftest.py`.
- Every behavioural claim needs a test that would have been RED before the change.

## Extension touch lists

Both lists below are complete, and the contract tests fail loudly when a step is missed:
`test_port_protocols_matches_settings_adapters` asserts SET EQUALITY between the Protocol
map and the `adapters:` keys, so a binding with no map entry AND a map entry with no
binding both fail; `test_adapter_constructs_with_single_settings_arg` catches a
constructor that does not follow the one convention.

### Adding an ADAPTER (a new implementation of an existing port)

1. **Write the class** in `src/enterprise_kb/adapters/<profile>/<name>.py`. One
   constructor convention: `Adapter(settings: Settings)`, nothing else.
2. **Keep cloud imports lazy.** Any `google-cloud-*` / `genai` / `adk` import lives
   inside a method or under `TYPE_CHECKING`, never at module top level, or the SDK-free
   profiles stop importing.
3. **Bind it** in `config/settings.yaml` under `adapters.<port>.<profile>` as a dotted
   `module.path:ClassName`. This is the build contract; there is no other registry.
4. **Speak in domain types only.** Return the `domain/models.py` (or `domain/kernel.py`)
   dataclasses; never leak an SDK object across the port.
5. **Cover the profile in the contract test** if you added a new profile family: extend
   `SDK_FREE_PROFILES` in `tests/contract/test_port_parity.py` only when the family
   really is SDK-free.
6. **Add behavioural parity** in `tests/contract/test_behavioral_parity.py` when the new
   adapter has a sibling that must agree with it (`local == platform`), or a
   deterministic-rerun / fail-fast proof when it does not.
7. **Unit-test the adapter itself** with the in-memory fakes; no network, no credentials.
8. **Run the gate.** `make lint test eval demo-selftest portability-demo`.

Failure mode if you skip step 3: `test_port_protocols_matches_settings_adapters` fails
with the missing key named. If you skip step 2,
`tests/contract/test_sdk_free_build.py` fails: it constructs every binding of EVERY
profile (`gcp` and `platform` included) in a fresh process where the `google` and
`vertexai` roots are refused by a meta-path finder, so a hoisted SDK import is caught
here even on a machine that never installed the `[gcp]` extra.

### Adding a PORT (a new capability the domain depends on)

1. **Define the Protocol** in the right `src/enterprise_kb/ports/*.py` module. It must be
   `@runtime_checkable` and take/return domain types only.
2. **Re-export it exactly once** from `ports/__init__.py`.
3. **Register it** in `PORT_PROTOCOLS` in `tests/contract/test_port_parity.py`.
4. **Bind every profile** in `config/settings.yaml` `adapters:`: `gcp`, `local`, and an
   `onprem` stub that fails fast (a port with no on-prem answer is a portability claim
   the repo cannot make).
5. **Add the on-prem stub** in `src/enterprise_kb/adapters/onprem/`, raising the domain
   error rather than returning a fake success.
6. **Wire it into the container** as a `cached_property` in `src/enterprise_kb/config.py`,
   and into the service constructor plus `api/deps.py` if a service consumes it.
7. **Document it** in `ARCHITECTURE.md` (the port table) and in `SPEC.md` if it changes a
   locked decision.
8. **Run the gate.**

Failure mode if you skip step 3 or 4: the set-equality drift guard names the port that is
bound-but-unregistered or registered-but-unbound. If you skip step 5, the on-prem
construction leg fails.

## Markdown

Avoid em-dashes in markdown (use colons / commas / parentheses). Validate any mermaid
diagram with `mmdc` before committing.

## Commits

Commits are authored solely by the contributor. Do not add co-author trailers.
