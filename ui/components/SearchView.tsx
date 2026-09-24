"use client";

import type { RetrievedPassage } from "@/lib/types";
import { AclTagBadge, Card, RedactionNotice } from "./ui";

/** Renders the ACL-filtered passages returned by `/v1/search`. */
export function SearchView({
  passages,
  inputRedacted = false,
}: {
  passages: RetrievedPassage[];
  /** Redaction changed the query before it was searched. */
  inputRedacted?: boolean;
}) {
  const notice = inputRedacted ? <RedactionNotice answered={false} /> : null;
  if (passages.length === 0) {
    return (
      <Card title="Passages">
        {notice}
        <p className="text-sm text-slate-500">
          No permitted passages. Either the corpus has no match, or the caller&rsquo;s
          principals do not resolve to any tag that admits a matching document.
        </p>
      </Card>
    );
  }
  return (
    <Card title={`Passages (${passages.length})`}>
      {notice}
      <ul className="space-y-3">
        {passages.map((p, i) => (
          <li key={`${p.citation.document_id}-${p.citation.page}-${i}`} className="rounded-lg border border-slate-200 p-3">
            <div className="mb-1 flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-xs font-semibold text-slate-700">
                [{p.citation.document_id} p.{p.citation.page}]
              </span>
              {p.acl_tags.map((t) => (
                <AclTagBadge key={t.label} tag={t} />
              ))}
            </div>
            <p className="text-sm text-slate-800">{p.text}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}
