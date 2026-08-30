import type { Metadata } from "next";
import "./globals.css";
import { ProvenanceBanner } from "../components/ProvenanceBanner";

export const metadata: Metadata = {
  title: "Enterprise Knowledge Base",
  description:
    "ACL-aware governed RAG over the bank corpus: cited, access-filtered passages and grounded answers.",
};

// Required by the nonce-based CSP in `lib/csp.mjs`, not a performance preference. Next can
// only stamp a per-request nonce onto the scripts of a DYNAMICALLY rendered route; a
// statically prerendered page was built before the nonce existed, so the browser blocks every
// script and the console renders as dead HTML. `assertHydratableCsp` fails the build if this
// line is removed.
export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // EMBED mode: when embedded in a host app (NEXT_PUBLIC_EMBED=1) the host owns the outer
  // chrome, so render the children bare (no min-height page wrapper / branding).
  const embed = process.env.NEXT_PUBLIC_EMBED === "1";
  // The banner renders in BOTH modes, and embedded is the mode that needs it most: a panel
  // inside somebody else's portal is where a viewer has least context about where the answer
  // came from. It is mounted in the LAYOUT rather than in a page because "at the top of every
  // page" is a property of the console, and a page that forgot it would be the one page a
  // screenshot came from.
  return (
    <html lang="en">
      <body className={embed ? undefined : "min-h-screen"}>
        <ProvenanceBanner />
        {children}
      </body>
    </html>
  );
}
