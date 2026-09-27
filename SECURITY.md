# Security

Runway holds bank access and financial history, so security reports are welcome and taken seriously.

## Reporting a vulnerability

Please report it privately through GitHub: the repository's **Security** tab → **Report a vulnerability**. Don't open
a public issue for a security problem. Include what you found, how to reproduce it, and the version (shown at the
bottom of Settings). You'll get an answer within a week, and a fix is released as soon as it's ready.

## Supported versions

Only the latest release gets security fixes. Update with `docker compose pull && docker compose up -d`.

## Running Runway safely

See [Putting Runway on the internet](DOCKER.md#putting-runway-on-the-internet) in DOCKER.md: HTTPS in front, sign-in
limited to you, a `RUNWAY_SECRET_KEY`, and private backups.
