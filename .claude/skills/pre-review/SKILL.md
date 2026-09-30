---
name: pre-review
description: Review this branch's changes the way the "Claude review" CI check will, before pushing, so its findings are fixed locally instead of costing a CI round each. Use before pushing to a pull request, or when asked to pre-review, self-review or check a branch before it goes up.
---

# Pre-review

The "Claude review" check (`.github/workflows/claude-review.yml`) reviews every push to a pull request, and each finding it leaves means another push and another full CI run. This runs the same review here first.

1. Read the review instructions from the source, not from memory: the `prompt:` block of `.github/workflows/claude-review.yml`. Use its review criteria and its P1/P2/P3 levels exactly. Ignore the parts about the pull request itself (`gh pr view`, `.claude-earlier-comments.md`, inline comments): here you review local changes and report back instead.
2. Read `AGENTS.md`, `CLAUDE.md` and `SECURITY.md`, as that prompt says.
3. Review what this branch changes against main: `git fetch origin main` then `git diff origin/main...HEAD`, plus anything uncommitted (`git diff HEAD`). Read the code around each change, not just the diff.
4. Report each finding as `**P1:**` / `**P2:**` / `**P3:**`, with `file:line`, what's wrong, why it matters and what to do instead, most severe first. Say so plainly if there's nothing worth raising.
5. Fix the P1 and P2 findings (and plainly correct P3s) before pushing, then run `make check`.

This doesn't replace the CI review, which still runs on the pushed commit; it catches most of what it would say one round earlier.
