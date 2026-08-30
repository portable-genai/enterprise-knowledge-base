/**
 * TypeScript mirrors of the A2 domain dataclasses.
 *
 * Source of truth: `src/enterprise_kb/domain/models.py`.
 * The backend serialises dataclasses with `domain/serialization.to_jsonable` (SPEC §5):
 * dataclass field names are preserved (snake_case) and every enum is rendered as its
 * `.value` string. These types follow that contract exactly.
 */

// --------------------------------------------------------------------------- //
// Access control
// --------------------------------------------------------------------------- //
export type PrincipalKind = "USER" | "GROUP" | "SERVICE";

export interface AclTag {
  label: string;
}

export type SourceSystem =
  | "confluence"
  | "sharepoint"
  | "gcs"
  | "file_upload"
  | "policy_portal"
  | "other";

// --------------------------------------------------------------------------- //
// Retrieval & citation
// --------------------------------------------------------------------------- //
export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Citation {
  document_id: string;
  title: string;
  uri: string;
  version: string;
  page: number | null;
  snippet: string;
  score: number | null;
  /** Layout-block locator ("p3#b2") when the claim resolved to a block, else null. */
  anchor?: string | null;
  /** Normalised page rectangle to highlight, present only alongside an anchor. */
  bbox?: BoundingBox | null;
}

export interface RetrievedPassage {
  text: string;
  citation: Citation;
  score: number;
  acl_tags: AclTag[];
}

export interface WebCitation {
  title: string;
  url: string;
  snippet: string;
}

// --------------------------------------------------------------------------- //
// Artifacts the API serves
// --------------------------------------------------------------------------- //
export interface SearchResponse {
  passages: RetrievedPassage[];
}

export interface GroundedAnswer {
  query: string;
  answer: string;
  citations: Citation[];
  web_citations: WebCitation[];
  confidence: number;
  // Maker-checker (P-06) is a floor: the service never returns this false for a
  // synthesised answer. `review_level` carries the escalation.
  requires_human_review: boolean;
  review_level: "standard" | "enhanced";
  review_reasons: string[];
  caveats: string[];
}

export interface RedactionFinding {
  info_type: string;
  count: number;
}

export interface IngestResult {
  document_id: string;
  chunks: number;
  ok: boolean;
  redaction_findings: RedactionFinding[];
  detail: string;
}

// --------------------------------------------------------------------------- //
// Corpus & health
// --------------------------------------------------------------------------- //
export type FreshnessStatus = "fresh" | "expired" | "missing" | "failed";

export interface FreshnessRecord {
  document_id: string;
  residency_region: string;
  fetched_at: string;
  expires_at: string;
  version: string;
  checksum: string;
  status: FreshnessStatus;
  source_authority: "direct" | "registry";
}

export interface CorpusStatus {
  total: number;
  fresh: number;
  expired: number;
  missing: number;
  failed: number;
  ttl_days: number;
  records: FreshnessRecord[];
}

export interface Health {
  // Provenance the banner states on every page: where the runtime sits and which model
  // answers. Both come from the service; nothing in the console infers either.
  runtime: string;
  generator_model: string;
  status: string;
  profile: string;
  region: string;
}

// --------------------------------------------------------------------------- //
// Demo identity (local profile only)
// --------------------------------------------------------------------------- //
// One seeded dev persona from GET /v1/personas. Empty list in secure profiles.
export interface Persona {
  id: string;
  subject: string;
  tenant: string;
  principals: string;
}

// --------------------------------------------------------------------------- //
// Governance : A2A AgentCard
// --------------------------------------------------------------------------- //
export interface AgentSkill {
  id: string;
  name: string;
  description: string;
}

export interface AgentCard {
  name: string;
  description: string;
  url: string;
  version: string;
  provider: string;
  skills: AgentSkill[];
}
