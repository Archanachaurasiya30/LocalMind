---
description: Draft a LocalMind PR description from the actual diff and verification evidence.
---

Read CONTRIBUTING.md, docs/AI_CONTRIBUTIONS.md and .github/pull_request_template.md.
Resolve the intended base/head and inspect the diff, including staged/unstaged changes if requested.
Draft a short title and fill the existing PR template without publishing or changing code.

Lead with the problem and resulting behavior. Give a concrete before/after example when useful.
List only changes present in the diff. Link an issue only when supplied or verified.
Use `Fixes #N` only for a complete fix. Keep limitations and untested platforms visible.

Record commands and results only from observed output or clearly attributed author reports.
If no evidence is available, write "Not run" and state the gap. Do not invent a successful test run.
Ask for missing AI-use and review history; do not infer "none" from the diff.
Count your own help drafting this PR as AI assistance. Do not invent human reviewers or claim
that multiple roles in one model constitute independent review. Leave incomplete declarations
unfinished and identify them to the author; a draft PR can keep them incomplete.

Use plain, specific language. Avoid promotional claims, unrelated history and long lists of implementation details.
Do not tick the author's human-review checkbox. Return the title and copyable body for their review.
