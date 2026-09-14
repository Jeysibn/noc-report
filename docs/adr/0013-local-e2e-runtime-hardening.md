# ADR 0013: Local E2E Runtime Hardening and Troubleshooting

## Status

Accepted — local development and E2E testing.

## Context

The local NOC stack runs the FastAPI API, an embedded transactional-outbox
dispatcher, RabbitMQ, the host-side Claude bridge, and an ephemeral Docker
sandbox. E2E testing exposed several process-boundary failures that did not
appear in isolated unit tests:

- `exec /usr/local/bin/python3: resource temporarily unavailable` when the
  sandbox applied `nproc=128` to the desktop user's host UID, because that UID
  already owned more than 128 host threads;
- Claude reporting `Not logged in` because the explicit host-UID override
  could not traverse the image's private `/home/sandbox` directory;
- queued jobs remaining unpublished after the outbox RabbitMQ channel closed;
- browser login failures when the UI was opened on `127.0.0.1:5173` while the
  API only allowed `localhost:5173`.

## Decisions

### Sandbox resources and identity

The real job sandbox keeps the bridge's non-root host UID so private
bind-mounted input, output, skill, and credential files remain readable only
by that UID. Docker's per-container `pids_limit=128` remains the process-tree
bound. The real job path does not apply a Linux `nproc` ulimit, because that
limit is accounted per host UID and includes unrelated desktop threads.

### Claude credentials

Only the copied `.credentials.json` is mounted read-only. It is mounted under
`/tmp/claude-home/.claude`, with `HOME=/tmp/claude-home`, because the image's
`/home/sandbox` directory is intentionally private to UID 10001 and is not
traversable by the host UID used for private bind mounts. The host's real
`~/.claude` directory is never mounted.

### Outbox recovery

The embedded dispatcher checks the Pika connection and channel before each
poll. If either is closed, it exits the inner polling loop and reconnects with
a fresh channel. Unpublished rows remain durable and are retried; a closed
channel must not cause an infinite retry loop against the same dead object.

### Local browser origins

Development CORS allows both `http://localhost:5173` and
`http://127.0.0.1:5173`. They are distinct browser origins even though both
resolve to the local machine.

## Operational checks

Start the infrastructure, API, frontend, and bridge. Verify:

```text
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8091/health
RabbitMQ consumers include noc.jobs.log-triage and noc.jobs.daily-report
```

If a job remains `QUEUED`, inspect its `outbox_events.published_at` and
`last_error` first. A healthy bridge cannot process a job that the outbox has
not published. If the job reaches the bridge but Claude returns
`budget_exhausted`, the configured per-job Claude budget was exceeded; this is
distinct from a worker or RabbitMQ failure.

## Known follow-up work

The generic JSON Schema contract verifies structure, not semantic usefulness.
Placeholder model output such as `"test"` can therefore be structurally valid.
Add skill-owned quality checks that reject placeholder or evidence-free
analysis before treating the result as operationally complete.
