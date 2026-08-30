"use client";

import { useEffect, useState } from "react";

import { api } from "../lib/api";

/**
 * The provenance this console states at the top of every page: WHERE it is running and
 * WHICH model answers (org decision, 2026-08-30).
 *
 * Not cosmetic. This knowledge base is demonstrated on a laptop and on the deployment, sometimes in
 * the same hour, and a screenshot of one is indistinguishable from the other. A viewer who
 * cannot tell which they are looking at cannot tell whether what they are reading came from
 * a real managed service or a deterministic offline stub, and that is exactly the confusion
 * an audit-first pitch cannot afford. The page says it, always, instead of the presenter
 * saying it sometimes.
 *
 * Both values come from `/healthz`; nothing here infers either. A console that read its own
 * runtime from `window.location` would be right until the deployment served through a proxy
 * and wrong silently after that.
 */

/** The wording, spelled once. The canonical copy lives in `hex-service-template`. */
export function provenance(runtime: string, model: string): string {
  const where = runtime === "gcp" ? "running on GCP" : "running locally";
  return `${where} · model ${model}`;
}

export function ProvenanceBanner() {
  const [origin, setOrigin] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .health()
      .then((status) => {
        if (!live || !status?.runtime) return;
        setOrigin(provenance(status.runtime, status.generator_model));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  // Null until the service has answered, and null again if it never does. A banner that
  // defaulted to "running locally" while the fetch was in flight would state a falsehood on
  // every deployment page load; a failed health call is reported by the console's own error
  // surface, and chrome that guessed would be asserting provenance it does not have.
  if (!origin) return null;
  return (
    <p className="border-b border-ink-200 bg-ink-50 px-4 py-1 text-xs text-ink-600">{origin}</p>
  );
}
