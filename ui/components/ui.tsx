"use client";

import type { AclTag } from "@/lib/types";

/**
 * Prominent maker-checker banner (General Principle P-06). Every synthesised answer is
 * gated for a checker: `requires_human_review` is a floor, not a switch. `level` carries
 * the escalation, so a low-confidence or sensitive-classification answer reads as
 * ENHANCED review rather than as the only reviewed case.
 */
export function HumanReviewBanner({
  reason,
  level = "standard",
}: {
  reason?: string;
  level?: "standard" | "enhanced";
}) {
  const enhanced = level === "enhanced";
  return (
    <div
      role="status"
      className={
        enhanced
          ? "flex items-start gap-3 rounded-lg border-l-4 border-amber-500 bg-amber-50 p-3.5 text-amber-900 ring-1 ring-inset ring-amber-200"
          : "flex items-start gap-3 rounded-lg border-l-4 border-slate-400 bg-slate-50 p-3.5 text-slate-800 ring-1 ring-inset ring-slate-200"
      }
    >
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="mt-0.5 h-5 w-5 shrink-0 text-amber-600"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path
          d="M12 9v4m0 4h.01M10.3 3.86 1.82 18a2 2 0 0 0 1.7 3h16.96a2 2 0 0 0 1.7-3L13.7 3.86a2 2 0 0 0-3.42 0Z"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">
            {enhanced ? "Enhanced human review required" : "Human review required"}
          </span>
          <span className="rounded-full bg-amber-200/70 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-800">
            Maker-Checker
          </span>
        </div>
        <p className="mt-0.5 text-xs leading-relaxed">
          {reason ??
            (enhanced
              ? "A hard signal raised the bar: this answer needs a second qualified reviewer before it can be relied upon."
              : "Every synthesised answer is queued for a checker before it can be relied upon. No hard signal was raised.")}
        </p>
      </div>
    </div>
  );
}

/** A small badge rendering an ACL tag carried by a passage (the label that admitted it). */
export function AclTagBadge({ tag }: { tag: AclTag }) {
  const restricted = /restricted|confidential|pii|mnpi|privileged/i.test(tag.label);
  const style = restricted
    ? "bg-rose-50 text-rose-700 ring-rose-200"
    : "bg-sky-50 text-sky-700 ring-sky-200";
  return (
    <span
      className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${style}`}
    >
      {tag.label}
    </span>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone = pct >= 75 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-semibold tabular-nums text-slate-700">{pct.toFixed(0)}%</span>
    </div>
  );
}

export function Card({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      {title ? <h2 className="mb-3 text-sm font-semibold text-slate-800">{title}</h2> : null}
      {children}
    </section>
  );
}
