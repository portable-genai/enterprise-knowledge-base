"use client";

import { useEffect, useState } from "react";
import { AnswerView } from "@/components/AnswerView";
import { ChatPanel } from "@/components/ChatPanel";
import { CorpusStatus } from "@/components/CorpusStatus";
import { SearchView } from "@/components/SearchView";
import { api, setDevPersona } from "@/lib/api";
import type { GroundedAnswer, Persona, RetrievedPassage } from "@/lib/types";

export default function Home() {
  const [passages, setPassages] = useState<RetrievedPassage[] | null>(null);
  const [answer, setAnswer] = useState<GroundedAnswer | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState("");

  // Demo persona picker: only relevant when the backend runs the local profile (no IdP).
  // In secure profiles health().profile !== "local", so the picker stays hidden and the
  // verified IAP identity is used instead.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const health = await api.health();
        if (health.profile !== "local") return;
        const list = await api.listPersonas();
        if (cancelled || list.length === 0) return;
        setPersonas(list);
        setSelectedPersona(list[0].id);
        setDevPersona(list[0].id);
      } catch {
        // Persona picker is dev-only convenience; ignore lookup failures.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function onPersonaChange(id: string) {
    setSelectedPersona(id);
    setDevPersona(id);
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">A2 Enterprise Knowledge Base</h1>
        <p className="mt-1 text-sm text-slate-600">
          ACL-aware governed RAG over the bank corpus. Every passage is access-filtered to
          the caller&rsquo;s principals and cited to its source page; grounded answers never
          go beyond the retrieved set.
        </p>
      </header>

      {personas.length > 0 ? (
        <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <label className="block text-xs font-medium text-slate-500">
            Demo identity (local profile)
          </label>
          <select
            value={selectedPersona}
            onChange={(e) => onPersonaChange(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-sm focus:border-slate-500 focus:outline-none sm:w-96"
          >
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.subject} &middot; {p.tenant}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-500">
            The verified persona supplies the audit actor and the entitlements scoping
            retrieval. Sent as <code>X-Dev-Persona</code>; ignored in secure profiles.
          </p>
        </section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <ChatPanel
            onPassages={(p) => {
              setPassages(p);
              setAnswer(null);
            }}
            onAnswer={(a) => {
              setAnswer(a);
              setPassages(null);
            }}
          />
          {answer ? <AnswerView answer={answer} /> : null}
          {passages ? <SearchView passages={passages} /> : null}
        </div>

        <aside className="space-y-6">
          <CorpusStatus />
        </aside>
      </div>
    </main>
  );
}
