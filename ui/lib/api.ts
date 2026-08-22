/**
 * Typed client for the A2 Enterprise Knowledge Base API (SPEC §6).
 *
 * The base URL defaults to the local dev server (`:8082`) and can be overridden with
 * `NEXT_PUBLIC_KB_API_URL`. Every method returns the snake_case JSON the backend emits
 * (see `lib/types.ts`), so the UI is a thin projection of the domain.
 *
 * Identity is server-verified: the client never sends an `actor`. In LOCAL mode the
 * backend resolves identity from the `X-Dev-Persona` header (the demo persona picker);
 * in secure profiles the header is ignored and identity comes from the IAP assertion
 * injected by the platform.
 */

import type {
  CorpusStatus,
  GroundedAnswer,
  Health,
  IngestResult,
  Persona,
  SearchResponse,
} from "./types";
import { apiBase } from "./api-base.mjs";

const BASE_URL = apiBase(process.env);

// Dev-only identity selection. Set from the local persona picker; attached as the
// X-Dev-Persona header only when non-empty. Ignored by the backend in secure profiles.
let devPersona = "";

export function setDevPersona(id: string): void {
  devPersona = id;
}

export function getDevPersona(): string {
  return devPersona;
}

function requestHeaders(base?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(base ?? {}) };
  if (devPersona) headers["X-Dev-Persona"] = devPersona;
  return headers;
}

/**
 * The backend REFUSES an answer that no permitted passage grounds (HTTP 422), rather
 * than returning an uncited one. That is a governed outcome, not a transport failure,
 * so it gets its own error type and its own message in the UI.
 */
export class UngroundedAnswerError extends Error {
  readonly detail: string;

  constructor(detail: string) {
    super(
      detail ||
        "No passage you are entitled to see grounds this question, so no answer was produced.",
    );
    this.name = "UngroundedAnswerError";
    this.detail = detail;
  }
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: requestHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    if (res.status === 422) {
      try {
        const parsed = JSON.parse(text) as { error?: string; detail?: string };
        if (parsed.error === "ungrounded") {
          throw new UngroundedAnswerError(parsed.detail ?? "");
        }
      } catch (e) {
        if (e instanceof UngroundedAnswerError) throw e;
        // fall through to the generic error below
      }
    }
    throw new Error(`${path} failed: ${res.status} ${text}`);
  }
  return (await res.json()) as T;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: requestHeaders() });
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export interface SearchInput {
  query: string;
  acl_principals: string[];
  top_k?: number;
}

export interface AnswerInput {
  query: string;
  acl_principals: string[];
}

export interface IngestInput {
  document_id: string;
  title: string;
  uri?: string;
  acl_tags: string[];
  version?: string;
  content_b64: string;
  mime_type?: string;
}

export const api = {
  search: (input: SearchInput) => postJSON<SearchResponse>("/v1/search", input),
  answer: (input: AnswerInput) => postJSON<GroundedAnswer>("/v1/answer", input),
  ingest: (input: IngestInput) => postJSON<IngestResult>("/v1/ingest", input),
  corpusStatus: () => getJSON<CorpusStatus>("/v1/corpus/status"),
  health: () => getJSON<Health>("/healthz"),
  listPersonas: () => getJSON<Persona[]>("/v1/personas"),
};

export { BASE_URL };
