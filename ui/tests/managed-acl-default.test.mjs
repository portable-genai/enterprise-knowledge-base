import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("managed query starts with no client ACL narrowing", () => {
  const panel = fs.readFileSync(new URL("../components/ChatPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /useState\(\"\"\)/);
  assert.doesNotMatch(panel, /useState\(\"group:risk\"\)/);
});
