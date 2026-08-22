# Adopting Hrz2

Hrz2 can be consumed as the shared governed knowledge service, connected to existing
institutional stores through adapters, or forked when independent ownership is required.
Prefer adapter and policy configuration over changing the domain contracts.

| Mode | Use when | Institution-owned changes |
|---|---|---|
| Consume Hrz2 | The shared search, answer and ingest contracts fit | Identity, corpus, policy, managed infrastructure |
| Implement ports | Existing search, DLP, model or audit services remain authoritative | Adapter classes and explicit profile bindings |
| Fork Hrz2 | Ownership, naming or release cadence must be independent | Rename, deployment, policy and regulator crosswalk |

Keep `domain/`, `ports/`, wire schemas and citation semantics stable. Institution-owned
surfaces include adapter bindings, identity policy, ACL directory mapping, corpus sources,
PII packs, thresholds, infrastructure and regulatory interpretation. Hrz1 owns shared safety,
Hrz3 agent discovery, Hrz4 promotion, Hrz5 cross-service audit and Hrz7 human decisions.
The current repository has no separately named kernel module, so its A7 audit remains partial.

## Preview and apply a rename

The upstream `enterprise-knowledge-base` token is shared by the Python distribution, CLI and cloud
resource names, so the utility uses one `--stem` for those surfaces.

```bash
python scripts/rename_fork.py \
  --package bank_knowledge \
  --stem bank-knowledge \
  --env-prefix BANK_KB \
  --include-docs --dry-run

python scripts/rename_fork.py \
  --package bank_knowledge \
  --stem bank-knowledge \
  --env-prefix BANK_KB \
  --include-docs --yes
```

The utility validates all output names, checks the package destination before any write and
previews by default. Recreate the editable environment after applying, then run `make check`.

## Human decisions before production

- Approve the identity and entitlement source; never accept client-asserted actor or ACL data.
- Approve tenant partitioning, corpus classification tags and source ownership.
- Select jurisdiction PII packs and maker-checker thresholds in institution-owned policy.
- Approve residency, CMEK, VPC-SC, WORM retention and external audit anchoring.
- Prove managed corpus export/import and reconciliation before claiming data portability.
- Replace every on-prem placeholder and retain the complete contract/eval/portability gate.

## Upstream and exit discipline

Add this repository as an `upstream` remote and integrate one released version at a time.
Resolve domain and wire-contract changes before institution-owned adapters. Never overwrite
local identity, ACL, PII, model, infrastructure or regulator policy. Exit proof requires
reconciled corpus migration, citation fidelity, audit export verification, restore evidence,
and operating approval; the current bounded script intentionally proves less.
