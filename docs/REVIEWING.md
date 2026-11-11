# Reviewing LocalMind changes

Anyone can review a PR or ask a question about the code. Only a maintainer with repository permission can merge it. Start with the issue, the diff and the checks for the current commit. Read [CODEBASE.md](CODEBASE.md) to locate callers and tests.

## What an author should expect

Keep one problem in a PR. Use a draft for unfinished work and questions about direction. Before marking it ready, fill in the PR template, run the applicable checks in [CONTRIBUTING.md](../CONTRIBUTING.md), and explain unavailable checks. A failing test caused by the change needs a fix. A runner outage needs a clear note rather than an unrelated code change.

Maintainer review time varies. After a week without a response, one polite follow-up in the same PR is reasonable. Do not ping unrelated contributors or open a duplicate PR. A reviewer who closes a PR should explain why and link any remaining work.

Reply to substantive feedback with the change made, evidence that the concern does not apply, or a question. Keep follow-up commits on the same branch. Avoid rewriting reviewed history unless necessary or agreed with the reviewer. Test the affected behavior again after a fix; do not claim that an old CI run covers a new commit.

## Team review

Use these perspectives for a code change. Skip a perspective only with a scope reason. One person or tool can perform multiple passes. Record whether they were sequential or independent.

| Perspective | LocalMind-specific checks |
| --- | --- |
| Behavior and context | Request IDs, cancellation, history order, follow-ups, project/conversation scope, fresh-source claims, model/provider capability checks. |
| Data and security | SQLite transactions and migrations, attachment ownership, credential storage, consent, paid-request retries, untrusted documents, path and network boundaries. |
| Tests and runtime | A regression that exposes the old bug, meaningful assertions, mocks versus live evidence, child-process ownership, Windows consoles, OS-specific packaging and cleanup. |
| Interface and accessibility | Keyboard use, focus, labels, full-window/mini-chat handoff, light/dark themes, loading/error/empty states, draft preservation and understandable errors. |

For the repeatable AI procedure, use [team-review/SKILL.md](../.github/skills/team-review/SKILL.md). It is a read-only review request; it does not grant permission to fix code or post comments. Local reports belong under ignored `artifacts/reviews/`. Put a short, sanitized summary in the PR so reviewers do not need private files.

Use existing CI evidence where it covers the reviewed commit. Run a focused test when it resolves a specific uncertainty. Do not run paid calls, download models, access personal profiles, or operate a contributor's desktop as an implied part of review.

## Useful findings

Name a reachable problem and point to the relevant file and line. Explain its effect and how to verify it. Distinguish a confirmed defect from a question. Keep formatting preferences separate from blocking feedback.

```text
Blocker | services/ai/app/example.py:42
When the request is retried after a timeout, the provider can receive it twice.
Evidence: trace the retry branch and stored request state.
Suggested check: simulate a timeout after the provider accepts the request.
```

This is an example format, not a claim about that file.

| Level | Meaning |
| --- | --- |
| Blocker | Confirmed security, data-loss, billing, or central correctness defect that must be resolved before merge. |
| Fix | A reachable bug or missing acceptance requirement that needs a change or a documented agreement. |
| Question | Evidence is incomplete; ask for the missing context or a targeted check. |
| Suggestion | Optional simplification or style preference. It does not block merge. |

Recheck each cited line before submitting findings. Separate pre-existing, unrelated defects into follow-up issues rather than expanding this PR. Report vulnerabilities privately under [SECURITY.md](../SECURITY.md).

## Before merge

- Confirm that the final diff matches the agreed scope and the author's description.
- Check current CI, resolved review threads, regression evidence and relevant native/manual checks.
- Confirm that documentation states any remaining limitations and migration or rollback requirements.
- Verify AI disclosure and the human review statement. Treat bot findings as evidence to investigate, not approval.

`Swarneil1905` is the current maintainer. For their own PRs, record a self-review and seek another human for high-risk changes when possible. Never describe self-review or multiple AI passes as independent human approval. Do not configure a mandatory approval rule that only the PR author could satisfy. See [maintainer setup](MAINTAINER_SETUP.md).

Merging source does not publish an installer. Release approval and checks remain separate in [RELEASING.md](RELEASING.md).
