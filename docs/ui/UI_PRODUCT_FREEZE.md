# UI Product Freeze

Per ADR-006 (UI-first), a full mocked frontend must exist and pass this
review before any backend implementation begins. Master plan §21 defines
the gate checklist this document tracks.

## Status

**Phase 1 ("Frontend Foundation + Full Mock Product", Milestones 0–7) is
implementation-complete as of 2026-09-09**, repo `/home/jeysi/noc-report`,
commit `ab2cd58`. This review was conducted against that commit. Most gate
items are met; a small number of explicit gaps are called out below rather
than checked off — see "Open items before Phase 2" for what genuinely
still needs attention, either now or as accepted debt.

## Freeze checklist (master plan §21)

- [x] App shell accepted — Command Deck sidebar/topbar/tokens (Milestone 1).
- [x] Dashboard accepted — shift card, stat cards, recent incidents, quick actions (Milestone 2).
- [x] Incident list accepted — filters, pagination, 120-incident mock dataset (Milestone 3).
- [x] Create Incident flow accepted — form + screenshot/log/evidence attach (Milestone 4).
- [x] OCR review flow accepted — full state machine, confidence flagging, field correction (Milestone 4).
- [x] Incident detail accepted — header, Overview, Evidence, Timeline, Log Analysis (Milestones 3, 5).
- [x] Log analysis workflow accepted — async job states, model/effort config, run history, skill's own result shape preserved (Milestone 5).
- [x] Shift report workflow accepted — readiness table, generation config, async generation, version history, DOCX placeholder (Milestone 6).
- [x] Knowledge base accepted — search/filter over mock incident corpus (Milestone 7).
- [x] Analytics scope accepted — V1 metrics only, bar+donut chart pairing (Milestone 7).
- [x] Admin scope accepted — Users, Roles, Shift Config, AI Config, Storage, Queue, Audit tabs (Milestone 7).
- [x] Role-specific navigation accepted — mock role switcher, Sidebar filtering, `RequireRole` route guard (NOC/DevOps/Admin) added post-Milestone-7 specifically to close this gate item.
- [~] Empty/loading/error states implemented — **partial, see below.**
- [x] Mock job behavior implemented — shared `runMockJob` simulator (`QUEUED→STARTING→RUNNING→COMPLETED/FAILED`) drives both Log Analysis and Shift Report; `JOB_TEST_CASES` documents forced outcomes.
- [x] Desktop layout accepted — all pages built and reviewed at desktop widths; no mobile/tablet layout work has been attempted or claimed.
- [x] Feature removals/additions documented — see "Deviations from the plan" below.
- [~] API-facing TypeScript contracts stabilized — **partial, see below.**

## Deviations from the plan (documented, not silently dropped)

- **Milestone 0.5 (retired provider Bridge Spike)** was inserted ahead of Milestone 1
  (Decision Log #2) to de-risk the sandbox lifecycle early; it uses a
  deterministic stand-in for the real retired provider CLI call rather than a live
  credentialed invocation (ADR-0001), scoped to `bridge/`/`sandbox/` only.
- **Analytics** is scoped to V1 metrics only, exactly as the plan
  specifies; V2 metrics (MTBA, semantic clusters, heatmaps, operator
  workload) are not built and are not implied to exist.
- **Shift Report generation** models "run missing analyses first" as a
  single mock job rather than literally orchestrating separate
  `log-triage-summary` jobs before the report job — the UI communicates
  the behavior (a warning line naming the count of incidents needing
  analysis) without simulating the sub-orchestration, since the real
  orchestration is Bridge/RabbitMQ behavior that doesn't exist until
  Milestones 11–14.
- **Role-based navigation** uses a mock, client-only role switcher (no
  session, no backend) — a deliberate stand-in so the RBAC nav gate item
  could be verified now rather than deferred whole to Milestone 8.

## Open items before Phase 2 (accepted debt, not silently passed)

- **Empty/loading/error states are inconsistent across pages.** Pages
  built directly against the async job simulator (Log Analysis, Shift
  Report) have real state machines with visible in-progress/failure UI.
  Pages that only call the mock services' `list()`/`get()` (Dashboard,
  Incident List, Knowledge Base, Analytics) render correctly once mock
  data resolves but do not have a distinct visible loading skeleton (mock
  calls resolve same-tick, so there's nothing to see in practice) or a
  handled network-error path, since the mock services never reject.
  **Recommendation:** revisit once Milestone 8 (Backend Core) makes
  real latency and real failures possible — building convincing
  loading/error states against a service that can't actually fail risks
  designing for the wrong failure shape.
- **API-facing TypeScript contracts are not yet stabilized.**
  `packages/contracts/src/index.ts` is still the Milestone 0 placeholder
  (`export {}`); the real domain types (`Incident`, `Shift`,
  `AnalysisResult`, `ReportVersion`, `AdminUser`, etc.) live in
  `apps/web/src/types/*` only, not in the shared package. **This should
  be closed before or during Milestone 8**, by moving/duplicating the
  contracts the backend will need to implement into
  `packages/contracts`, rather than treating it as done.
- **7 npm audit vulnerabilities** (5 moderate / 1 high / 1 critical) have
  been present in `apps/web`'s dependency tree since Milestone 0 and were
  never remediated. Flagged repeatedly, never blocking, still open.
- **Skill versioning scheme** (git hash vs. semver vs. content hash,
  Decision Log #3) remains undecided; needs resolution before Milestone
  12 (retired provider Bridge), not before Phase 2 starts generally.

## Stakeholder sign-off

Not yet recorded — this document reflects a self-review against the
master plan's §21 checklist, conducted as part of the same autonomous
implementation pass that built Milestones 0–7. A human stakeholder
sign-off (name + date) should be added here before Phase 2 backend work
begins, per ADR-006's intent that this gate involve actual review, not
only an implementer's own checklist.

- [ ] Stakeholder sign-off recorded here with date.
