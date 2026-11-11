# Contributing to LocalMind

LocalMind is a desktop app built with React, Tauri, Python and Ollama. You can help with a reproducible bug report, a documentation correction, a test, an interface improvement or a code change. You do not need to understand every part of the app to contribute.

Start with the [codebase guide](docs/CODEBASE.md) to find the part you want to work on. The [desktop development guide](apps/desktop/README.md) covers installation and running from source. For product behavior and current limitations, read the [README](README.md).

Before opening a PR, read the [review guide](docs/REVIEWING.md) and [AI contribution policy](docs/AI_CONTRIBUTIONS.md). AI tools are welcome. Disclose their use, review the diff yourself, and report what you actually tested. You do not need a paid AI subscription to contribute.

## Choose a first contribution

Look through [open issues](https://github.com/Swarneil1905/LocalMind/issues) and [pull requests](https://github.com/Swarneil1905/LocalMind/pulls) before starting, so you can avoid duplicating work. If an issue fits, leave a short comment explaining what you plan to investigate. An issue report is useful even if you cannot write the fix.

Good first changes include reproducing a bug in a test, correcting a setup step, improving an error message, or fixing keyboard behavior in one component. Keep the change small enough that someone can understand the problem and review the fix together.

If someone is already working on an issue, ask before starting a competing fix. Start with one contribution while you learn the review process. A draft PR is enough to share progress. If you need a break, leave a note; there is no automatic assignment deadline. Maintainers should check in before reassigning work.

You can open a pull request directly for a small fix. For a new feature, dependency, storage change or broad refactor, open an issue first to discuss the use case and approach. This is especially useful for anything that downloads models, sends data to a service, spends money or changes files on disk.

## Set up your fork

1. Fork [LocalMind](https://github.com/Swarneil1905/LocalMind) to your GitHub account.
2. Clone your fork, replacing `YOUR-USERNAME` below.
3. Add the original repository as `upstream` and create a branch for your change.

```sh
git clone https://github.com/YOUR-USERNAME/LocalMind.git
cd LocalMind
git remote add upstream https://github.com/Swarneil1905/LocalMind.git
git switch -c fix/short-description
```

Follow [desktop setup](apps/desktop/README.md#set-up-the-workspace) from the dependency-installation step; you have already cloned the repository. CI uses Node.js 22, pnpm 11.0.9, Python 3.11 and Rust stable. The guide includes native prerequisites and the development sidecar placeholder required by Tauri.

You can work on documentation without installing the app. Frontend unit tests use mocked desktop calls and do not need a downloaded model. Running the full app requires the native prerequisites, Python service and Ollama. Optional media and connector features have additional setup; they are not prerequisites for a first contribution.

### Use a development profile

Before launching the app, set a separate profile so experiments do not use your everyday conversations and files. Run this from the repository root in the same terminal used to start the app.

**PowerShell:**

```powershell
$env:LOCALMIND_DATA_DIR = Join-Path (Get-Location) 'artifacts/dev-profile'
```

**macOS / Linux:**

```sh
export LOCALMIND_DATA_DIR="$PWD/artifacts/dev-profile"
```

Then launch with your Python environment active:

```sh
pnpm --filter desktop tauri dev
```

The profile path must be absolute. Two services cannot use the same profile at once. This separates LocalMind's profile data; it does not create a separate OS account or Ollama model store. Use synthetic documents and test accounts for experiments. See [storage and runtime behavior](docs/DESKTOP_RUNTIME.md).

## Make and check the change

For a bug fix, add a regression test that fails for the reported behavior and passes with the fix. Include skipped inputs, cancellation, missing data or scope changes when they are part of the bug. Avoid unrelated formatting changes or lockfile updates.

Use the checks for the areas you changed. Run commands from the repository root unless the block changes directory. The workflows in [.github/workflows](.github/workflows) are the source of truth for CI.

### Interface and frontend

```sh
pnpm --filter desktop typecheck
pnpm --filter desktop lint
pnpm --filter desktop test
pnpm --filter desktop build
```

For interface changes, also try keyboard navigation, visible focus, light and dark themes, and both full-window and mini chat when affected. Include screenshots or a short recording in the PR. Check empty, loading and error states as well as a successful response. The [tester checklist](docs/product/tester-checklist.md) has broader manual checks.

### Python service

With the Python environment active:

```sh
cd services/ai
python -m ruff check .
python -m unittest discover -s tests -v
python -c "import app.main; import app.api.routes.health"
cd ../..
```

For a focused run while developing, select a test file, for example:

```sh
cd services/ai
python -m unittest discover -s tests -p "test_memory_link_indices.py" -v
cd ../..
```

Use the broader service checks before submitting a backend change. Keep tests independent of personal profiles, live accounts and paid providers. Describe native or live-provider checks separately when those are needed.

### Rust and desktop lifecycle

Complete the native prerequisites and sidecar-placeholder step in the setup guide first.

```sh
cargo check --workspace
cargo clippy --workspace -- -D warnings
cargo test --workspace
```

Process and installer behavior can differ by OS. Report which platform you tested and any skips. Passing on one machine does not establish that a Windows process fix or macOS installer works on its native platform.

### Model behavior and evaluation

```sh
python scripts/evaluation/check_suite.py
python -m unittest discover -s scripts/evaluation -p "test_*.py" -v
```

For prompt, retrieval or model-setting changes, follow the [evaluation guide](evaluation/README.md). Compare the old and new behavior using the same model, settings and cases. Report failures as well as improvements, and keep held-out cases separate from examples used to tune the change.

Evaluation cases measure behavior; they do not train the model. See [dataset preparation](evaluation/DATASETS.md) before importing data. Keep private conversations, downloaded datasets and generated run reports out of commits. Reusable synthetic cases and evaluation code belong in the repository; local outputs belong under the ignored `artifacts/` directory.

### Packaging and release tooling

```sh
python -m ruff check --config services/ai/pyproject.toml scripts/release
python -m unittest discover -s scripts/release -p "test_*.py" -v
python scripts/release/check_version.py
```

Some tests require platform tools and will skip elsewhere. Record that accurately. Consult [Releasing LocalMind](docs/RELEASING.md) for packaging changes; a frontend build is not an installer test. A normal contribution does not need a release tag, version bump or publishing credentials.

For documentation-only changes, check that linked files exist, commands match the current setup and examples contain no private data. You do not need to run the full application test suite for a prose edit.

## Open a pull request

Review the diff and stage only the files that belong to your change:

```sh
git diff
git add path/to/changed-file path/to/test-file
git diff --cached
git commit -m "Fix memory links after skipped facts"
git push -u origin HEAD
```

Replace the example paths and commit message with your change. On GitHub, open a PR from your branch to `Swarneil1905/LocalMind`'s `main` branch. The PR template asks for:

- The problem and a concrete example of the affected behavior.
- What the change does and any remaining limitations.
- The checks you actually ran, their results and any skipped checks.
- AI tools used for implementation, tests, documentation or review, or an explicit statement that none were used.
- The team-review result, or a reason it was not run, and your own review of the diff.

Use `Fixes #123` when the PR fully resolves that issue, or `Related to #123` when work remains. Review the **Files changed** tab for accidental files, credentials and unrelated edits. Wait for CI and address failures before asking for a final review. If the change is still in progress, open a draft PR.

Review feedback is part of the work. Reply with what you changed or why you chose another approach, then push follow-up commits to the same branch. If you use an AI coding tool, check its suggestions, understand the diff and verify its claims before submitting. Only list tests you actually ran.

The [PR description prompt](.github/prompts/pr-description.prompt.md) can help draft a description from your diff and test results. It does not publish anything. Keep the template headings and fill in its disclosure fields; the PR readiness check reports missing answers. Draft PRs can leave them unfinished.

Reviewers check correctness, data boundaries, tests and usability. They do not judge authorship from writing style. See [REVIEWING.md](docs/REVIEWING.md) for finding severity, handling disagreements, and merge responsibilities. `Swarneil1905` is the current maintainer; an AI review is not a second human reviewer.

## Report a problem without writing code

Open a [bug report](https://github.com/Swarneil1905/LocalMind/issues/new) with the app version or commit, OS, steps to reproduce, expected behavior and actual result. Include a small example that someone else can try.

For response-quality problems, include the model tag, response preset, relevant settings, Memory/Knowledge/Web toggles, and the conversation turns needed to reproduce the problem. A sanitized **Response details** diagnostic can help identify the model and timing. Separate an incorrect answer from a rendering problem, missing context or failed service request when you can.

Remove credentials, private messages, personal documents and identifying file paths from reports and screenshots. Report security vulnerabilities privately using [SECURITY.md](SECURITY.md), not a public issue.

## Keep the documentation useful

If your change adds a setting, command, network request or limitation, update the relevant guide in the same PR. Update the [codebase map](docs/CODEBASE.md) when moving responsibility between modules. Existing code, tests and CI should agree with the instructions a contributor follows.
