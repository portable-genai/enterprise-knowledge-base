"use client";

import { useState } from "react";
import { api, UngroundedAnswerError } from "@/lib/api";
import type { GroundedAnswer, RetrievedPassage } from "@/lib/types";

type Mode = "search" | "answer";

/**
 * The query panel: pick a mode (search vs grounded answer), enter the query and the
 * caller's ACL principals (comma-separated), and submit. Access scoping is explicit so the
 * demo makes the ACL-filtering behaviour visible.
 */
export function ChatPanel({
  onPassages,
  onAnswer,
}: {
  onPassages: (p: RetrievedPassage[]) => void;
  onAnswer: (a: GroundedAnswer) => void;
}) {
  const [mode, setMode] = useState<Mode>("answer");
  const [query, setQuery] = useState("");
  // Empty means "use the full server-verified scope". A client hint may narrow but never
  // invent an entitlement; a managed IAP user does not automatically hold demo groups.
  const [principals, setPrincipals] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    const acl_principals = principals
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      if (mode === "search") {
        const res = await api.search({ query, acl_principals });
        onPassages(res.passages);
      } else {
        const res = await api.answer({ query, acl_principals });
        onAnswer(res);
      }
    } catch (e) {
      setError(e instanceof UngroundedAnswerError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex gap-2">
        {(["answer", "search"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-md px-3 py-1 text-sm font-medium ${
              mode === m
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {m === "answer" ? "Grounded answer" : "Search passages"}
          </button>
        ))}
      </div>

      <label className="block text-xs font-medium text-slate-500">Query</label>
      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        rows={2}
        placeholder="What due diligence is required before onboarding a cloud provider?"
        className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-sm focus:border-slate-500 focus:outline-none"
      />

      <label className="mt-3 block text-xs font-medium text-slate-500">
        ACL principals (comma-separated)
      </label>
      <input
        value={principals}
        onChange={(e) => setPrincipals(e.target.value)}
        placeholder="group:risk, user:jane@bank.test"
        className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-sm focus:border-slate-500 focus:outline-none"
      />

      <button
        onClick={submit}
        disabled={busy || !query.trim()}
        className="mt-3 rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
      >
        {busy ? "Working..." : mode === "answer" ? "Answer" : "Search"}
      </button>

      {error ? <p className="mt-2 text-xs text-rose-600">{error}</p> : null}
    </section>
  );
}
