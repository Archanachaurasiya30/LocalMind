import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { checkReadiness, sections, summary } from "./pr_readiness.mjs";

const body = `## What changed and why
Retain source indices when an earlier memory is filtered out.
## Validation
Focused regression passed locally. Native platforms not tested.
## AI usage
Usage: assisted
Tools and scope: Copilot suggested a test; I reviewed the fix.
## Review
Team review: completed
Review details: Commit abc1234; sequential behavior/data/test/UI passes. Fixed one finding; no live model call.
- [x] I read the final diff and can explain the change and its limitations.
`;
const event = (text = body, extra = {}) => ({ pull_request: { body: text, draft: false, user: { login: "contributor", type: "User" }, ...extra } });

test("accepts a completed disclosure", () => assert.deepEqual(checkReadiness(event()).errors, []));
test("no-AI contribution does not need a tool name", () => {
  const text = body.replace("Usage: assisted", "Usage: none").replace(/^Tools and scope:.*$/m, "Tools and scope:");
  assert.deepEqual(checkReadiness(event(text)).errors, []);
});
test("a contributor without an AI subscription can explain a manual review", () => {
  const text = body.replace("Team review: completed", "Team review: not run")
    .replace(/^Review details:.*$/m, "Review details: No review tool available. I traced both callers and ran the regression test.");
  assert.deepEqual(checkReadiness(event(text)).errors, []);
});
test("docs-only scope can explain why a code team review is inapplicable", () => {
  assert.deepEqual(checkReadiness(event(body.replace("Team review: completed", "Team review: not applicable"))).errors, []);
});
test("unfinished template fails as ready but is allowed as a draft", () => {
  const template = readFileSync(new URL("../../.github/pull_request_template.md", import.meta.url), "utf8");
  assert.ok(checkReadiness(event(template)).errors.length >= 5);
  assert.ok(checkReadiness(event(template, { draft: true })).skipped);
});
test("hidden HTML guidance does not satisfy missing answers", () => {
  const text = body.replace("Usage: assisted", "<!-- Usage: assisted -->");
  assert.ok(checkReadiness(event(text)).errors.some(error => error.startsWith("Set Usage")));
});
test("missing tools or human review is reported", () => {
  const text = body.replace(/^Tools and scope:.*$/m, "Tools and scope: TBD").replace("[x]", "[ ]");
  assert.equal(checkReadiness(event(text)).errors.length, 2);
});
test("review not run still needs a reason", () => {
  const text = body.replace("Team review: completed", "Team review: not run").replace(/^Review details:.*$/m, "Review details:");
  assert.ok(checkReadiness(event(text)).errors.some(error => error.startsWith("Add Review details")));
});
test("headings inside fenced validation commands do not replace sections", () => {
  const text = body.replace("Focused regression passed locally.", "~~~sh\n## AI usage\nnode --test scripts/contributing/*.test.mjs\n~~~\nFocused regression passed locally.");
  assert.equal(sections(text).get("ai usage").includes("Usage: assisted"), true);
  assert.deepEqual(checkReadiness(event(text)).errors, []);
});
test("duplicate declarations are rejected rather than silently replaced", () => {
  assert.equal(checkReadiness(event(body + "\n## AI usage\nUsage: none")).errors.length, 1);
});
test("only named dependency bot accounts bypass the template", () => {
  for (const login of ["dependabot[bot]", "renovate[bot]"]) {
    assert.ok(checkReadiness(event("", { user: { login, type: "Bot" } })).skipped);
    assert.ok(checkReadiness(event("", { user: { login, type: "User" } })).errors.length);
  }
  assert.ok(checkReadiness(event("", { user: { login: "other[bot]", type: "Bot" } })).errors.length);
});
test("untrusted text is never reflected into workflow commands or the summary", () => {
  const text = "$(touch /tmp/nope)\n::error::pretend failure\n<script>bad</script>";
  const report = summary(checkReadiness(event(text)));
  assert.ok(!report.includes("touch")); assert.ok(!report.includes("::error::")); assert.ok(!report.includes("<script>"));
});
test("CRLF and checked uppercase X are supported", () => {
  assert.deepEqual(checkReadiness(event(body.replace("[x]", "[X]").replaceAll("\n", "\r\n"))).errors, []);
});
test("missing and oversized PR bodies fail clearly", () => {
  for (const text of [null, "x".repeat(65_537)]) assert.ok(checkReadiness(event(text)).errors.length);
  assert.ok(checkReadiness({}).skipped);
});
