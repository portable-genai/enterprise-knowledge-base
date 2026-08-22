"use client";

import type { GroundedAnswer } from "@/lib/types";
import { CitationCard } from "./CitationCard";
import { Card, ConfidenceMeter, HumanReviewBanner } from "./ui";

/** Renders a grounded answer from `/v1/answer` with provenance and the review gate. */
export function AnswerView({ answer }: { answer: GroundedAnswer }) {
  return (
    <Card title="Grounded answer">
      <div className="space-y-3">
        {answer.requires_human_review ? (
          <HumanReviewBanner
            level={answer.review_level}
            reason={
              answer.review_reasons.length > 0
                ? `Escalated by: ${answer.review_reasons.join(", ")}.`
                : undefined
            }
          />
        ) : null}

        <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
          {answer.answer}
        </p>

        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-slate-500">Confidence</span>
          <ConfidenceMeter value={answer.confidence} />
        </div>

        {answer.caveats.length > 0 ? (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-700">
            {answer.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        ) : null}

        {answer.citations.length > 0 ? (
          <div className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Citations
            </h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {answer.citations.map((c, i) => (
                <CitationCard key={`${c.document_id}-${c.page}-${i}`} citation={c} />
              ))}
            </div>
          </div>
        ) : (
          <p className="text-xs text-slate-500">(no citations)</p>
        )}
      </div>
    </Card>
  );
}
