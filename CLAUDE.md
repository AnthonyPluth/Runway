# Claude Code notes

Repo conventions, commands and layout are in [AGENTS.md](AGENTS.md); read it first.

## Model routing

Pick the cheapest model that can do the job well. When delegating to a subagent, set its `model` accordingly:

- **Haiku**: file and symbol searches, listing usages, summarizing a file, boilerplate, mechanical renames, docs typos.
- **Sonnet**: normal implementation, bug fixes with a clear cause, writing or updating tests, small refactors, PR review of routine changes.
- **Opus**: architecture and design decisions, hard or unclear debugging, changes to the forecast, money math, migrations or the Plaid/bank-sync flow, and anything security-sensitive (auth, OIDC, `secretbox`, encryption, backups).

When unsure, start one tier lower and escalate if the first attempt misses. Don't spawn a subagent for a task that takes a couple of tool calls to do inline.
