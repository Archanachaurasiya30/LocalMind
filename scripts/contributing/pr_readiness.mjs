import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

// This validates declarations, not their truth or the safety of a change.
export function sections(body) {
  const result = new Map();
  let heading = "", fence = null;
  for (const line of body.replace(/<!--[\s\S]*?-->/g, "").split(/\r?\n/)) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
    }
    const title = !fence && !marker && line.match(/^##\s+(.+?)\s*$/);
    if (title) {
      heading = title[1].toLowerCase();
      if (result.has(heading)) throw new Error("Use each PR template heading only once.");
      result.set(heading, "");
    } else if (heading) result.set(heading, result.get(heading) + line + "\n");
  }
  return result;
}

function meaningful(text) {
  const value = (text || "").trim();
  return Boolean(value && !/^(?:todo|tbd|n\/?a|none|\.\.\.|-+)\.?$/i.test(value));
}

function field(section, name) {
  return section?.match(new RegExp(`^${name}:[ \\t]*(.*)$`, "im"))?.[1]?.trim() || "";
}

export function checkReadiness(event) {
  const pr = event.pull_request;
  if (!pr) return { skipped: "No pull request in this event.", errors: [] };
  if (pr.draft) return { skipped: "Draft PR: complete the template before requesting review.", errors: [] };
  if (pr.user?.type === "Bot" && ["dependabot[bot]", "renovate[bot]"].includes(pr.user.login)) {
    return { skipped: "Dependency bot: a maintainer reviews scope, tests and provenance.", errors: [] };
  }
  if (typeof pr.body !== "string" || pr.body.length > 65_536) {
    return { errors: ["Fill in the PR template with a body of at most 65,536 characters."] };
  }
  let parts;
  try { parts = sections(pr.body); }
  catch (error) { return { errors: [error.message] }; }
  const errors = [];
  for (const heading of ["what changed and why", "validation"]) {
    if (!meaningful(parts.get(heading))) errors.push(`Complete the '${heading}' section, including limitations or checks not run.`);
  }
  const usage = parts.get("ai usage"), review = parts.get("review");
  const declaration = field(usage, "Usage").toLowerCase();
  if (!["none", "assisted"].includes(declaration)) errors.push("Set Usage to 'none' or 'assisted' under AI usage.");
  if (declaration === "assisted" && !meaningful(field(usage, "Tools and scope"))) {
    errors.push("Name the AI tools and what they helped with under Tools and scope.");
  }
  const state = field(review, "Team review").toLowerCase();
  if (!["completed", "not run", "not applicable"].includes(state)) {
    errors.push("Set Team review to 'completed', 'not run', or 'not applicable'.");
  }
  if (!meaningful(field(review, "Review details"))) {
    errors.push("Add Review details: commit/tools/findings, or the reason and checks performed instead.");
  }
  if (!/^- \[x\] I read the final diff and can explain the change and its limitations\.\s*$/im.test(review || "")) {
    errors.push("Read the final diff, then check the human-review statement in the Review section.");
  }
  return { errors };
}

export function summary(result) {
  if (result.skipped) return `## PR readiness\n\n${result.skipped}\n`;
  return "## PR readiness\n\n" + (result.errors.length
    ? result.errors.map(error => `- ${error}`).join("\n") + "\n\nEdit the PR description; this check runs again after an edit. See CONTRIBUTING.md.\n"
    : "Required declarations are present. This is not a code review, proof of AI-use disclosure, or approval to merge.\n");
}

export function main(env = process.env) {
  const result = checkReadiness(JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8")));
  const report = summary(result);
  console.log(report);
  if (env.GITHUB_STEP_SUMMARY) appendFileSync(env.GITHUB_STEP_SUMMARY, report);
  return result.errors.length ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { process.exitCode = main(); }
  catch { console.error("PR readiness could not read the event. Check the workflow configuration."); process.exitCode = 1; }
}
