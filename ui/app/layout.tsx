import type { Metadata } from "next";
import "./globals.css";

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
  return (
    <html lang="en">
      <body className={embed ? undefined : "min-h-screen"}>{children}</body>
    </html>
  );
}
