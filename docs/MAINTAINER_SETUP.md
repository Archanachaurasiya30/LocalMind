# Contributor workflow setup

The repository provides contribution and review docs, a PR template, a team-review skill, Copilot instructions, and a PR readiness workflow. Files alone do not enable hosted Copilot review or protect a branch. This guide separates those settings from the code.

## Activate the repository files

1. Review and merge the contributor-workflow change into `main` through a PR.
2. Open a small test PR from a fork. Confirm that GitHub inserts the updated PR template.
3. Keep it a draft while the description is incomplete. Mark it ready, confirm the declaration check reports missing fields, then fill them in and confirm the edited event reruns it.
4. Check that ordinary frontend, Python and Rust CI still run. Use the check names GitHub actually reports when configuring requirements.
5. Confirm that `.github/CODEOWNERS` requests `Swarneil1905` for contributor changes. Do not add people or teams who have not agreed to review.

The readiness workflow uses a read-only token, no provider credentials and no paid model calls. It reads PR metadata from the event file, not through shell interpolation. It posts a check summary, not repetitive PR comments. It has no permission to merge, approve, label or write repository files.

It validates declarations, not whether they are true. Draft PRs and the named Dependabot/Renovate bot accounts skip the declaration requirement; their code still needs CI and maintainer review. The workflow checks PR source using ordinary unprivileged `pull_request` CI. A PR can propose changes to the checker, so a human must review workflow and policy changes. Do not convert it to `pull_request_target` or give it secrets/write permissions while checking out PR code.

Local checks for this workflow:

```sh
node --test scripts/contributing/*.test.mjs
```

## Branch rules

In GitHub repository settings, create or update a ruleset for `main`. Require PRs, the relevant passing CI checks, and resolved review conversations. Add `Contributor declarations` only after the workflow reports that exact context successfully. Do not make checks with path filters mandatory unless they run on every applicable PR.

There is currently one named maintainer. GitHub does not let authors approve their own PRs. Requiring that same person's approval on every PR would block their own work. Keep human merge responsibility documented now; enable required independent approvals when another willing reviewer has repository access. If an existing ruleset requires reviews, do not weaken it to accommodate this guide: arrange a reviewer or use an explicitly documented emergency process.

Keep bypass access limited. Record the reason and checks when using a bypass. Never substitute an AI approval for an independent human approval. Do not grant a review bot write access to source or publishing credentials.

## Optional GitHub Copilot review

Manual team review and CI are the default. Contributors can use their existing AI tools or explain why AI review was not run. No subscription is required for this route.

If you choose hosted Copilot review, sign in as **Swarneil1905**, not the company account. Confirm eligibility and billing in GitHub before buying anything. Review requests send repository context to the hosted service and can consume AI credits and Actions minutes.

The repository already supplies `.github/copilot-instructions.md` and `.github/skills/team-review/SKILL.md`. Start with a manual Copilot review on one small PR. Inspect its findings before enabling automatic reviews.

For automatic reviews, GitHub's current setup uses **Settings → Rulesets → branch rules → Automatically request Copilot code review**. Target `main`. Initially leave draft reviews and review-on-every-push off to control noise and usage. Under **Settings → Copilot → Code review**, keep auto-approval and counting Copilot approvals toward merge requirements disabled. Retain a human merge decision.

Instructions guide the reviewer but do not enforce security. GitHub currently reads review instructions from the PR head, so changes to those instructions need human scrutiny too. Do not configure unrelated MCP servers, secrets or project execution hooks for a reviewer just to provide more context.

No Copilot subscription, repository setting or paid reviewer is activated by this change.

## Maintenance

Treat confirmed bot findings like other review findings. Explain dismissed false positives. Do not auto-merge because a model reports no issues. When CI or setup commands change, update CONTRIBUTING.md and the relevant codebase/review guidance in the same PR.

This workflow takes inspiration from [Wheelgap's contributor guide](https://github.com/wheelgap/wheelgap/blob/main/CONTRIBUTING.md) and [Apache Iggy's contributor guide](https://github.com/apache/iggy/blob/master/CONTRIBUTING.md). LocalMind's instructions and implementation are written for its desktop architecture; neither project's policies apply here.

GitHub references, checked November 10, 2026:

- [PR templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository)
- [Code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
- [Configure Copilot review](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-code-review)
- [Customize Copilot review](https://docs.github.com/en/copilot/tutorials/customize-code-review)
- [Agent skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills)
