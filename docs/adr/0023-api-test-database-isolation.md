# 0023 — Isolate destructive API fixtures from development data

- Status: **accepted**
- Date: 2026-09-16

## Context

The API tests deliberately drop and recreate the PostgreSQL schema for every
test. Running them with the development `DATABASE_URL` destroyed live local
incidents, jobs, and report data.

## Decision

API tests require an explicit `TEST_DATABASE_URL`, and it must match
`DATABASE_URL` for the test process so the application and fixtures share the
same disposable database. CI provisions a separate `noc_report_test`
database. Test documentation now shows the disposable-database command.

## Consequences

Accidental test runs against the live development database fail immediately.
Developers must create or provision the test database before running the API
suite, which is an intentional safety barrier.
