# Security and Data Privacy

## Why `.env` stays local

`.env` holds the Square access token (`SQUARE_ACCESS_TOKEN`). Square tokens grant API access to a real merchant account — in Sandbox that's a fake test account, but the same code path is used for Production later. `.env` is listed in `.gitignore` and must never be committed, pasted into chat, printed, or logged. Configuration is loaded through `src/retailpulse/config.py`, which types the token as `pydantic.SecretStr` so it can't be accidentally interpolated into a log line or `repr()`.

## Why raw Square data stays local

Bronze JSON under `data/bronze/` is the *unmodified* API response. Depending on the entity, that can include order line items, payment records, and (in Production) real customer and transaction details. None of that belongs in a public or even private portfolio repo. `data/bronze/`, `data/silver/`, and `data/gold/` are all Git-ignored; only `data/.gitkeep` (an empty placeholder) is tracked, so the directory structure exists on a fresh clone without any data in it.

## What may be committed

- Source code (`src/`, `tests/`)
- Configuration templates with placeholder values only (`.env.example`)
- SQL schema definitions (`sql/`)
- Documentation (`docs/`, `README.md`)
- CI configuration that does not reference real credentials (`.github/workflows/`)
- Synthetic or aggregated sample data explicitly generated for demonstration purposes

## What may never be committed

- `.env` or any file containing a real Square access token
- Raw Square API responses (`data/bronze/**`, `data/silver/**`, `data/gold/**`)
- Vendor/acquisition cost data (`data/input/**`) — real supplier pricing is business-sensitive
- Real customer, employee, or vendor names, emails, phone numbers, or payment identifiers
- Screenshots that reveal a token, authorization header, or unredacted business data
- Production credentials, in code, CI, or GitHub Actions secrets, during this milestone

## If a token is accidentally exposed

1. Immediately revoke/rotate it in the Square Developer Console (Credentials section of the application) — this invalidates the exposed token instantly.
2. Issue a new Sandbox (or Production) token and update your local `.env` only. Never reuse an exposed token.
3. If it was committed to Git, treat the commit as compromised even after a history rewrite — assume the token was seen and rely on revocation, not history-scrubbing, as the actual fix.
4. Check the Square Developer Console / Dashboard for any unexpected API activity during the exposure window.

Revocation is the only reliable fix. Deleting a commit or force-pushing does not un-expose a secret that may already have been cloned, cached, or indexed — the token itself must be invalidated at the source.

## Why production data should not appear in GitHub Actions

This milestone's CI (`.github/workflows/ci.yml`) runs unit tests, linting, and the security check — none of which need network access to Square. No `SQUARE_ACCESS_TOKEN` secret is configured in the repository, so CI cannot reach Square even accidentally. Keeping Production tokens out of CI entirely (during this milestone) avoids the risk of a misconfigured workflow, a compromised Action, or a public fork silently gaining read access to a real store's transaction data.
