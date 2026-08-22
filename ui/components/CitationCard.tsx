"use client";

import type { Citation } from "@/lib/types";

/**
 * Renders one citation. Provenance is the whole point of A2: every passage and grounded
 * claim points to a source document, version and page so a reviewer can verify it, and to
 * the layout block plus highlight box when the claim resolved to one. A citation with no
 * anchor is valid, coarser provenance and is shown at page level, never as an error.
 */
export function CitationCard({ citation }: { citation: Citation }) {
  const page = citation.page != null ? ` p.${citation.page}` : "";
  const box = citation.bbox;
  const version = citation.version && citation.version !== "unknown" ? ` ${citation.version}` : "";
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-xs font-semibold text-slate-700">
          [{citation.document_id}
          {version}
          {page}]
        </span>
        {citation.anchor ? (
          <span
            className="rounded bg-slate-200 px-1.5 py-0.5 font-mono text-[11px] text-slate-700"
            title={
              box
                ? `highlight box ${box.x0}, ${box.y0} to ${box.x1}, ${box.y1}`
                : "block anchor"
            }
          >
            block {citation.anchor}
          </span>
        ) : null}
        {citation.score != null ? (
          <span className="text-[11px] tabular-nums text-slate-500">
            score {citation.score.toFixed(2)}
          </span>
        ) : null}
      </div>
      <p className="mt-1 font-medium text-slate-800">{citation.title}</p>
      {citation.snippet ? (
        <p className="mt-1 line-clamp-2 text-xs text-slate-600">&ldquo;{citation.snippet}&rdquo;</p>
      ) : null}
      {citation.uri ? (
        <a
          href={citation.uri}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-block text-xs text-sky-600 hover:underline"
        >
          {citation.uri}
        </a>
      ) : null}
    </div>
  );
}
