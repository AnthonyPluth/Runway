# Security

Runway holds bank access and financial history, so security reports are welcome and taken seriously.

## Reporting a vulnerability

Please report it privately through GitHub: the repository's **Security** tab → **Report a vulnerability**
([open a private report](https://github.com/AnthonyPluth/Runway/security/advisories/new)). Don't open
a public issue for a security problem. Include what you found, how to reproduce it, and the version (shown at the
Settings → Advanced). You'll get an answer within a week, and a fix is released as soon as it's ready.

## Supported versions

Only the latest release gets security fixes. Update with `docker compose pull && docker compose up -d`.

## Running Runway safely

See [Putting Runway on the internet](DOCKER.md#putting-runway-on-the-internet) in DOCKER.md: HTTPS in front, sign-in
limited to you, a `RUNWAY_SECRET_KEY`, and private backups.

## One household, not one account per person

Runway has no accounts of its own: sign-in decides who gets in, and everyone who gets in (`OIDC_ALLOWED_EMAILS`,
`OIDC_ALLOWED_GROUPS`) sees and can change everything. That includes the bank connections and API keys, the browser
extension's key, the assistants anyone connected, the "let assistants change churning" switch, the backup (which
holds the bank access and API keys, encrypted with `RUNWAY_SECRET_KEY`) and restoring one. An account's owner and a
card's owner are labels that say whose something is, not who may see it. That's by design: it's built for one person
or one household. Don't let in anyone you wouldn't hand your finances to, and don't share one Runway between
households.
