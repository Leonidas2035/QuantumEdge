# Security Model

## Core rules

- All writes go through `write_engine.apply_change_set_with_policy`.
- `warn` / `block` verdicts never apply automatically.
- `dry_run` never applies.
- Approve/apply is allowed only for `warn` runs and re-runs gates in shadow.

## Secret handling

- Project scanning uses deny globs for `.env`, keys, and secrets.
- Prompt building masks common secret patterns.
- Gate step env keys containing `KEY`, `SECRET`, `TOKEN`, or `PASSWORD` are rejected.

## UI and API safety

- Control Center binds to `127.0.0.1` by default.
- API requests require `X-CC-Token`.
- Do not expose the UI port to the public internet.

## Operational recommendations

- Keep `runtime/` out of git.
- Store tokens and credentials outside the repo.
- Review patches and gate logs before approve/apply.
