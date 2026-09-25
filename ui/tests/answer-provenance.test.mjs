/**
 * The model pills' source: what answered, read off the console's own API responses.
 *
 * These import `lib/answer-provenance.mjs` itself, so a rule that changes in the component
 * changes here too. What is checked is that a pill can never name a model no response named,
 * that another origin's response cannot set it, and that the one `fetch` wrapper is installed
 * once and put back.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { answerOf, isConsoleApi, watchAnswers } from "../lib/answer-provenance.mjs";

const HERE = "http://console.test/";
const STANDALONE = "http://api.test:8082";
const PORTAL = "/knowledge/api";

function reply(headers) {
  return { headers: new Headers(headers) };
}

function host(headers) {
  const calls = [];
  const original = async (input) => {
    calls.push(input);
    return reply(headers);
  };
  return { fetch: original, original, calls, location: { href: HERE } };
}

test("a response that names no model is no answer, never a guess", () => {
  assert.equal(answerOf(new Headers()), null);
  assert.equal(answerOf(new Headers({ "x-search-used": "true" })), null);
  assert.equal(answerOf(new Headers({ "x-answered-by": "  " })), null);
});

test("the answering model and the search flag are read as sent", () => {
  assert.deepEqual(answerOf(new Headers({ "x-answered-by": "gemini-3.5-flash" })), {
    model: "gemini-3.5-flash",
    search: false,
  });
  assert.deepEqual(
    answerOf(new Headers({ "x-answered-by": "a, b", "x-search-used": "true" })),
    { model: "a, b", search: true },
  );
});

test("only this console's own API base counts, standalone or under the portal", () => {
  assert.equal(isConsoleApi(STANDALONE + "/v1/answer", HERE, STANDALONE), true);
  assert.equal(isConsoleApi(new URL(STANDALONE + "/healthz"), HERE, STANDALONE + "/"), true);
  assert.equal(isConsoleApi({ url: STANDALONE + "/v1/answer" }, HERE, STANDALONE), true);
  assert.equal(isConsoleApi("http://elsewhere.test:8082/v1/answer", HERE, STANDALONE), false);
  assert.equal(isConsoleApi("/knowledge/api/v1/answer", HERE, PORTAL), true);
  assert.equal(isConsoleApi("/knowledge/apisomething", HERE, PORTAL), false);
  assert.equal(isConsoleApi("/static/app.js", HERE, PORTAL), false);
  assert.equal(isConsoleApi(42, HERE, PORTAL), false);
});

test("an empty base is this console's own origin, and only that", () => {
  assert.equal(isConsoleApi("/v1/answer", HERE, ""), true);
  assert.equal(isConsoleApi("http://console.test/healthz", HERE, ""), true);
  assert.equal(isConsoleApi(STANDALONE + "/v1/answer", HERE, ""), false);
});

test("the wrapper reports an answer from the console's API and ignores other origins", async () => {
  const window = host({ "x-answered-by": "model-a", "x-search-used": "true" });
  const seen = [];
  const stop = watchAnswers(window, STANDALONE, (answer) => seen.push(answer));
  await window.fetch(STANDALONE + "/v1/answer", { method: "POST" });
  await window.fetch("http://elsewhere.test/v1/answer");
  stop();
  assert.deepEqual(seen, [{ model: "model-a", search: true }]);
  assert.equal(window.calls.length, 2, "the wrapper must still perform every call");
});

test("the wrapper is installed once however many listeners, and restored by the last", async () => {
  const window = host({ "x-answered-by": "model-b" });
  const first = [];
  const second = [];
  const stopFirst = watchAnswers(window, PORTAL, (answer) => first.push(answer.model));
  const wrapped = window.fetch;
  const stopSecond = watchAnswers(window, PORTAL, (answer) => second.push(answer.model));
  assert.equal(window.fetch, wrapped, "a second listener wrapped fetch again");
  await window.fetch("/knowledge/api/v1/answer");
  stopFirst();
  assert.equal(window.fetch, wrapped, "the wrapper left while a listener remained");
  stopSecond();
  assert.equal(window.fetch, window.original, "the original fetch was not put back");
  assert.deepEqual(first, ["model-b"]);
  assert.deepEqual(second, ["model-b"]);
});
