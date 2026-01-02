# Control Center (Local UI)

## Start

```
python meta_agent.py ui --port 8766 --bind 127.0.0.1
```

Open: `http://127.0.0.1:8766`

The UI is token-protected. The server injects a runtime token into the page and the browser sends
it via `X-CC-Token`. No token is printed to stdout.

## What it does

- Create TaskSpecs and enqueue them into `runtime/inbox`.
- Inspect runs, reports, patches, and gate outputs.
- Approve & Apply for `warn` verdicts only (re-runs shadow + gates before applying).
- View scheduler status and toggle schedule enable/disable.

## Security notes

- The server binds to `127.0.0.1` by default.
- API calls require the `X-CC-Token` header.
- No secrets are printed or logged.

## Troubleshooting

- **Port busy**: run with `--port 8787`.
- **403 forbidden**: refresh the page to obtain a new token.
- **Missing runs**: verify `runtime/runs/<run_id>/report.json` exists.
