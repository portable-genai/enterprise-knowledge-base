// Fail the UI gate when shipped code collapses unset and configured-empty environment values.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const UI_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SCANNED_EXTENSIONS = [".mjs", ".js", ".jsx", ".ts", ".tsx", ".mts", ".cts"];
const SKIPPED_DIRECTORIES = new Set(["node_modules", ".next", "tests", ".git", "out"]);
const THREE_STATE_READER_MODULE = join("lib", "env-setting.mjs");

export function scannedSources(root = UI_ROOT) {
  const found = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const full = join(root, entry.name);
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRECTORIES.has(entry.name)) found.push(...scannedSources(full));
    } else if (SCANNED_EXTENSIONS.some((extension) => entry.name.endsWith(extension))) {
      found.push(full);
    }
  }
  return found.sort();
}

/** Remove comments while retaining line numbering and the literals exact comparisons use. */
export function codeOnly(source) {
  let out = "";
  let index = 0;
  while (index < source.length) {
    const pair = source.slice(index, index + 2);
    if (pair === "//") {
      while (index < source.length && source[index] !== "\n") index += 1;
      continue;
    }
    if (pair === "/*") {
      index += 2;
      while (index < source.length && source.slice(index, index + 2) !== "*/") {
        if (source[index] === "\n") out += "\n";
        index += 1;
      }
      index += 2;
      continue;
    }
    const quote = source[index];
    if (quote === '"' || quote === "'" || quote === "`") {
      out += quote;
      index += 1;
      while (index < source.length && source[index] !== quote) {
        if (source[index] === "\\") {
          out += source[index];
          index += 1;
        }
        if (index < source.length) {
          out += source[index];
          index += 1;
        }
      }
      out += quote;
      index += 1;
      continue;
    }
    out += source[index];
    index += 1;
  }
  return out;
}

const DOTTED_READ = /(^|[^\w$.])(?:process\s*\.\s*)?env\s*\.\s*([A-Za-z_$][\w$]*)/g;
const INDEXED_READ =
  /(^|[^\w$.])(?:process\s*\.\s*)?env\s*\[\s*(?:"([^"]*)"|'([^']*)'|([^\]]+))\]/g;

function isExactMatch(code, endIndex) {
  return /^\s*[=!]==?\s*["'`]/.test(code.slice(endIndex));
}

export function findings(source) {
  const code = codeOnly(source);
  const lineOf = (index) => code.slice(0, index).split("\n").length;
  const found = [];
  for (const [pattern, nameGroups] of [
    [DOTTED_READ, [2]],
    [INDEXED_READ, [2, 3, 4]],
  ]) {
    pattern.lastIndex = 0;
    let match = pattern.exec(code);
    while (match !== null) {
      const literal = nameGroups.slice(0, 2).find((group) => match[group] !== undefined);
      const name = literal === undefined ? "<computed at runtime>" : match[literal];
      if (!isExactMatch(code, match.index + match[0].length)) {
        found.push({ line: lineOf(match.index + match[1].length), name });
      }
      match = pattern.exec(code);
    }
  }
  return found.sort((left, right) => left.line - right.line);
}

const shipped = scannedSources().filter(
  (path) => relative(UI_ROOT, path) !== THREE_STATE_READER_MODULE.split("/").join(sep),
);

test("the scanner walks the shipped UI", () => {
  const names = shipped.map((path) => relative(UI_ROOT, path));
  for (const required of ["lib/api-base.mjs", "lib/csp.mjs", "next.config.mjs"]) {
    assert.ok(names.includes(required.split("/").join(sep)), `${required} is not scanned`);
  }
});

for (const path of shipped) {
  const name = relative(UI_ROOT, path);
  test(`no two-state environment read in ${name}`, () => {
    const offenders = findings(readFileSync(path, "utf8")).map(
      (read) => `${name}:${read.line}: reads ${read.name} directly`,
    );
    assert.deepEqual(
      offenders,
      [],
      "Use readEnvSetting from lib/env-setting.mjs or an exact literal comparison:\n" +
        offenders.join("\n"),
    );
  });
}

test("the scanner goes red for wildcard and nullish-default mutants", () => {
  assert.deepEqual(findings('const allowed = env.UI_ORIGINS || "*";\n'), [
    { line: 1, name: "UI_ORIGINS" },
  ]);
  assert.deepEqual(findings('const allowed = process.env.UI_ORIGINS ?? "*";\n'), [
    { line: 1, name: "UI_ORIGINS" },
  ]);
});

test("exact fail-closed opt-ins and the shared reader are accepted", () => {
  assert.deepEqual(findings('const enabled = env.ALLOW_INSECURE === "1";\n'), []);
  assert.deepEqual(findings('const configured = readEnvSetting(env, "UI_ORIGINS");\n'), []);
});
