"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CorpusStatus as CorpusStatusType } from "@/lib/types";
import { Card } from "./ui";

const STATUS_TONE: Record<string, string> = {
  fresh: "text-emerald-600",
  expired: "text-amber-600",
  missing: "text-slate-500",
  failed: "text-rose-600",
};

/** Live summary of the freshness + residency ledger (`/v1/corpus/status`). */
export function CorpusStatus() {
  const [status, setStatus] = useState<CorpusStatusType | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .corpusStatus()
      .then(setStatus)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <Card title="Corpus freshness">
        <p className="text-sm text-rose-600">Could not load ledger: {error}</p>
      </Card>
    );
  }
  if (!status) {
    return (
      <Card title="Corpus freshness">
        <p className="text-sm text-slate-500">Loading...</p>
      </Card>
    );
  }
  return (
    <Card title={`Corpus freshness (${status.total} documents, TTL ${status.ttl_days}d)`}>
      <div className="mb-3 flex gap-4 text-xs">
        <span className={STATUS_TONE.fresh}>fresh {status.fresh}</span>
        <span className={STATUS_TONE.expired}>expired {status.expired}</span>
        <span className={STATUS_TONE.failed}>failed {status.failed}</span>
      </div>
      <ul className="space-y-1 text-xs">
        {status.records.map((r) => (
          <li key={r.document_id} className="flex items-center justify-between gap-2">
            <span className="font-mono text-slate-700">{r.document_id}</span>
            <span className={STATUS_TONE[r.status] ?? "text-slate-500"}>
              {r.status} &middot; {r.source_authority} &middot; {r.residency_region}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
