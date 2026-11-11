# AI-assisted contributions

You can use AI to investigate a problem, write code, add tests, edit docs or review a change. The person submitting the PR remains responsible for it. This policy applies to maintainers too.

## Disclose the work

Fill in **AI usage** and **Review** in the PR template. Use `Usage: none` when no AI tool helped with the contribution, including its description and review. Otherwise use `Usage: assisted` and name the tools and the work they did. Include a model name when known; do not guess it.

A useful disclosure is short:

```text
Usage: assisted
Tools and scope: Copilot suggested the parser change and two tests. I revised the error handling and wrote the regression fixture.
```

Do not estimate a percentage of AI-written code. Do not publish private prompts, personal conversations, credentials or full agent transcripts. A disclosure describes assistance; it is not a claim about copyright or a substitute for checking dependency and source licenses.

Read every changed file, trace the affected behavior, and check generated claims against the code. Be able to explain the failure, the fix and what remains untested. If a tool ran a command, inspect its result before reporting it as passed. CI results count only for the commit they tested.

## Review AI-assisted code before requesting review

For code changes, use the [team-review procedure](REVIEWING.md#team-review) with your existing tool if it is available. The [portable skill](../.github/skills/team-review/SKILL.md) defines the review. You can give that file to a tool that does not discover repository skills automatically.

The procedure covers behavior, data/security, tests/runtime and interface/accessibility. These are review perspectives, not four invented people. Separate agents can inspect them independently when your tool supports that. Otherwise run sequential passes and say that you used one tool. Do not describe sequential passes as independent reviews.

Validate each finding. Fix confirmed defects, explain rejected findings, and list gaps. Another model agreeing with the first does not prove a defect. After changes, check affected findings against the final diff and record the reviewed commit. Uncommitted work must be identified as a working-tree review, then checked again before submission.

Use the template states honestly:

| Team review | What to report |
| --- | --- |
| `completed` | Reviewed commit, tools and perspectives used, confirmed findings and their disposition, untested areas. |
| `not run` | Why it was unavailable and the manual review or tests performed instead. This does not automatically reject a contribution. |
| `not applicable` | Why the scope does not need a code team review, such as a link correction. Still read the final diff. |

You do not need to buy a subscription. A maintainer can request a focused additional review when a change affects credentials, stored data, paid requests, process ownership or release publishing.

## Human responsibility

AI suggestions are advisory. They cannot authorize data access, a paid request, a merge, a release or a change to repository permissions. A person checks findings and makes the merge decision. Do not submit an agent's claim of approval as a human approval or fabricate reviewer names.

LocalMind does not use an AI detector to decide whether a contribution is acceptable. Reviewers assess the diff, evidence and the author's ability to explain it. A missing disclosure can be corrected. Unexplained bulk changes, invented test results or repeated unverified responses can result in a request to narrow or close the PR, with a reason.

Only send code to a hosted reviewer when you are allowed to share it. Use synthetic fixtures. Keep private security reports in the process described in [SECURITY.md](../SECURITY.md).
