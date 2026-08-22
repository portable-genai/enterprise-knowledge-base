"use client";

import type { IngestResult } from "@/lib/types";
import { Card } from "./ui";

/** Renders the outcome of a `/v1/ingest` call: chunks indexed + what was redacted. */
export function IngestView({ result }: { result: IngestResult }) {
  return (
    <Card title="Ingest result">
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
              result.ok
                ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                : "bg-rose-50 text-rose-700 ring-rose-200"
            }`}
          >
            {result.ok ? "indexed" : "failed"}
          </span>
          <span className="font-mono text-xs text-slate-700">{result.document_id}</span>
        </div>
        <p className="text-slate-700">
          {result.chunks} chunk{result.chunks === 1 ? "" : "s"} indexed.
        </p>
        {result.redaction_findings.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Redacted before index (P-04)
            </h3>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {result.redaction_findings.map((f) => (
                <li
                  key={f.info_type}
                  className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700"
                >
                  {f.info_type} &times;{f.count}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {result.detail ? <p className="text-xs text-slate-500">{result.detail}</p> : null}
      </div>
    </Card>
  );
}
