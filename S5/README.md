# S5 · Factory UI

The only user-facing console for the **DHIS2 App Studio / Metadata Factory**. It shows, at any
moment, the current project & stage, produced artifacts, approved conclusions, open uncertainties,
items to confirm, the next executable action, which DHIS2 environment an action affects, and the
final deliverables (总体 SRS 第 18 章). S5 holds **no business state** — all state and action
legality is owned by **S1**.

This subsystem implements `requirements/S5_FactoryUI_SRS.md` (V1.5.0).

## Scope

The console covers the whole surface in SRS §1.2 — views that older releases only routed are now
implemented, not deferred:

- **Cold start** — `/getting-started` is a four-step readiness path (models & providers, environment,
  project, run the pipeline), one step per screen with the registration form inline; the Dashboard
  repeats the first unfinished step.
- **Projects** — list, new-project wizard (mode A/B, type, languages, target environments, design
  reference, promotion order), project settings (targets, mode upgrade, notifications, archive,
  export/import, delete).
- **Workspace** — horizontal stage flow and five sections: **Overview / Design / Results / Delivery /
  Manage**. Design holds stages, gates, inputs, column mapping and the model log; Results holds
  artifacts, issues, metadata, decisions, the object graph and the API document; Delivery holds
  handoff, deployments and change requests; Manage holds members, read authorisations, production
  exemptions, audit and project settings.
- **Questions** — the stage's open questions as one page: single/multiple choice, recommended
  suggestions, “Other” text, blocking validation, one submit, next round rendered in place.
- **Authoring & approval** — manual patch via a requirements table or a schema-aware Monaco JSON
  editor; six-question gate page with warning dispositions, seen hashes, confirmations and approval
  progress; in-stage chat drawer on the four dialogue stages.
- **Review & delivery** — artifact review (media-type-aware preview, diff, lineage, reproduce,
  download), metadata/registry, object graph, decisions, read-only API document, handoff package,
  deployment runs and run console with COMMIT confirmation and post-commit outcome.
- **Operations** — change requests, audit chain verification and COMMIT trace, notifications.
- **Platform** — environments and capability profiles, governance (models, providers, policy,
  prompts, cost), accounts & roles, system settings, login/OIDC and forced password change.

Where S1 exposes no capability the console says so **on the control that is missing**, by leaving it
out and documenting it (`user-guide.md` §11). There is no page-preview tab because S1 exposes no page
preview, and no API “try it” control because only S4 may talk to DHIS2.

## Tech stack (mandated §0)

TypeScript · React 18 · Vite · React Router · TanStack Query · Zustand · React Hook Form · Zod ·
`@dhis2/ui` · React Flow · Monaco Editor · a small in-repo OpenAPI reader (`js-yaml` +
`src/lib/openapi/*`) for the project's API document.

React is pinned to **18** because `@dhis2/ui@10` peer-requires `^16.13 || ^18`.

## How it talks to S1

- S5 calls **only** the S1 public REST API (`/api/v1`) and its SSE stream (UI-IF-001, C-01). It
  never touches a database, DHIS2, a model provider, or any subsystem other than S1.
- The vendored contract is **1.19.0 / 147 paths** (`contract/openapi.json`). The HTTP client and the
  **Zod validation models are generated from it** — `openapi-typescript` for the typed client,
  `scripts/codegen-zod.ts` for the Zod models (UI-APP-007). Regenerate after the contract changes:
  `npm run codegen`. `src/test/contractVersion.test.ts` pins both the version string and the path
  count, so a stale vendored copy fails `npm test` instead of silently dropping a field.
- S5 is deployed **same-origin** with `/api/v1` behind one reverse proxy (UI-APP-008); see
  `deploy/nginx.conf`.

## Core rules encoded

| Rule | Where |
| --- | --- |
| Button availability comes **only** from `allowed-actions` (UI-BR-01) | `src/api/actions.ts`, `AllowedActionButton.tsx` |
| Never infer business state from events/local (UI-BR-02) | `src/sse/*`, `src/stores/ui.ts` |
| No credential value held locally — pass-through once, reference afterwards (UI-BR-03, UI-ADM-002) | `features/secrets/CredentialField.tsx`, `EnvironmentsView.tsx` (UI-AC-06/13) |
| Operator on every state change; disable actions without it (UI-BR-04) | `stores/operator.ts`, middleware |
| COMMIT is never auto-resubmitted on network failure (UI-BR-05) | `mutations.useCommitRun`, `RunConsole.tsx` (UI-AC-07) |
| Cold start is a stated path, not a guess — one step per screen (UI-SET-001…006) | `features/setup/SetupSteps.tsx` + `SetupView.tsx` (`setupSteps`), route `/getting-started` |
| Approve handles every WARNING, carries seen hashes and confirmation items (UI-BR-06) | `features/workspace/gate/GatePage.tsx` (UI-AC-04/05) |
| One questionnaire submit, blocking questions enforced (UI-BR-07, UI-Q-004) | `features/workspace/questions/QuestionCard.tsx`, `StageView.tsx`, `mutations.useAnswerQuestions` |
| Artifact bytes follow media type; purged bytes show a receipt (UI-BR-08, UI-REV-001) | `features/review/ArtifactReview.tsx`, `lib/media.ts` |

`GET /api/v1/setup` (contract 1.19.0, S5IF-SET01) is the only source for the readiness checklist:
subsystem wiring real/fake, `validate_runtime()`'s configuration problems, environments registered but
never connection-tested, model and project counts, and whether the credential store is switched on
(`credentials`). A section S1 withholds arrives as `null` and renders as
"not visible to you" — never as zero. `/getting-started` is **one step per screen**: the current step
opens the registration it needs inline (`ProviderForm` / `ModelForm` / `EnvironmentForm`, the same
components the Administration pages render — `FormShell.tsx` switches between modal and embedded), so
there is one definition of a valid registration rather than two that drift. `providers` is a fact
card, not a step: it is the server's `FACTORY_PROVIDER_MODE` and no browser control can change it.

A credential value crosses the browser exactly once. `CredentialField` posts it to
`PUT /api/v1/secrets/{name}`, keeps the returned `db://name` reference and drops the value — including
on failure, and including the idempotency intent, which does not digest the body. `GET` never returns
a value, a mask or a length (S5IF-G09); `GET /secrets` lists names only, and there is no
`GET /secrets/{name}` to probe.

Question answers go out as **one** `POST /api/v1/questions/answers` per questionnaire (UI-Q-004):
`QuestionView.answerMode` picks radios or checkboxes, `SuggestionView.recommended` marks the model's
recommendation, “Other” carries free text, and blocking questions keep the submit disabled. S1 records
all answers in one transaction and resumes the run at most once; the response's `questions` are the
still-open ones, so the next round appears in the same card after the question queries are
invalidated. Run and answer requests carry `Accept-Language`, so new questions follow the interface
language.

Governance edits are differential: `PATCH /governance/models/{ref}` sends only changed fields and never
`endpoint` or `apiKeySecretRef`; the provider/model identity is locked while editing, and an unchanged
form cannot be saved. Provider rows also offer **test connection**
(`POST /governance/providers/{name}/test-connection`, S5IF-GOV20), which separates an unreachable
address from a rejected key from a slow model without producing a model call.

Server state is served exclusively through TanStack Query (`src/api/queryClient.ts`); Zustand keeps
only UI state (operator name, language, layout, selection, filters).

## Navigation

The console is organised around the work, not around its tables:

- **Work** — Dashboard, Awaiting approval, Get started.
- **Projects** — the project list; inside a project: **Overview / Design / Results / Delivery /
  Manage**. The stage flow is the horizontal stepper above those five sections. A stage and a gate
  belong to Design; artifacts, issues, metadata, decisions, the object graph and the API belong to
  Results; handoff, deployments and change requests belong to Delivery; members, read
  authorisations, exemptions, audit and settings belong to Manage.
- **Platform** — Environments, Governance (`/governance/models|providers|policy|prompts|cost`),
  Accounts & permissions (`/users`, `/roles`), Settings.
- **Top bar** — notifications (with the unread count), interface language, the operator's own
  actions. **Help** opens the workflow map, glossary and role guide; every page header carries a
  page-specific help entry, and `/help/tutorial` walks one real upload through to a verified delivery.

Every project route remains addressable. There is no second stage rail and no permanent inspector:
the stage flow carries the sequence, and page content gets the full width instead of a three-column
squeeze.

## Requirements → source

- i18n + no hardcoded strings, both locales key-parity enforced at compile time and by
  `src/i18n/messages.test.ts` (UI-APP-002, UI-AC-08). `src/i18n/keysUsed.test.ts` reads the source:
  every literal `t('a.b')` must resolve in **both** languages. `src/lib/vocabulary.test.ts` pins the
  shared enum vocabulary (§2.1); unknown tokens are displayed as sent, never blanked.
- Error envelope → `errorCode` mapped to a localised message + `correlationId` + retry (UI-REQ-002,
  `src/api/errors.ts`, `src/i18n/index.ts`). `404 NOT_GENERATED` with `requiredStage` is resolved as
  “nothing produced yet”: the blueprint draft read resolves it to no draft (no global error), and
  the object graph and handoff screens explain which stage produces it instead of showing a failed
  request (`api/errors.isNotGenerated`).
- Idempotency keys reused per logical action; COMMIT pending-confirmation (UI-REQ-001/004,
  `src/api/idempotency.ts`).
- SSE reconnect replays from the last sequence, `stream.reset` refreshes the project root keys,
  progress is surfaced, offline banner after >10s (UI-RT-001..005,
  `src/sse/useWorkflowEvents.ts`). Progress frames are read by the names S1's reporter writes —
  `step`, `completedFraction`, `agentRunId` (`S1/src/factory_control/progress.py`) — not by a
  `completed`/`total` pair the protocol never carried; the cancel button's run id comes from the same
  frame.
- Artifact review avoids downloading what it cannot render: `lib/media.ts` decides text vs binary
  (media type first, uploaded filename as fallback), a purged artifact shows the purge receipt, and
  the download link is gated by `artifact.download` (UI-REV-001, UI-BR-08).
- Runtime config `public/config.js` is **not** baked into the bundle (UI-APP-005).

## Develop

```bash
npm ci
npm run codegen          # regenerate client types + Zod from the vendored OpenAPI
npm run dev:mock         # standalone with the MSW mock + fake SSE (UI-NFR-05)
npm run dev              # against a real S1 (proxies /api/v1 → VITE_S1_PROXY)
```

`npm run dev:mock` needs no backend. The mock data lives in `src/mocks/db.ts` and the handlers in
`src/mocks/handlers.ts`; `src/mocks/events.ts` simulates the SSE stream.

## Test & verify

```bash
npm run verify           # the gate: codegen → typecheck → lint → coverage → build
npm run typecheck        # tsc over src
npm run lint             # eslint (flat config)
npm test                 # Vitest unit + component tests (MSW-backed)
npm run test:coverage    # thresholds 80 statements / 75 branch / 80 function / 80 line
npm run test:e2e         # Playwright main-flow (UI-NFR-04) — needs `npx playwright install chromium`
npm run a11y             # Playwright + axe-core WCAG 2.1 AA (UI-NFR-03)
npm run build            # production bundle → dist/
npm run build:e2e        # mock-enabled bundle for the e2e webServer → dist-e2e/
```

`VITE_ENABLE_MOCK` is a **build-time** property baked into the bundle, so the directory it lands in is
what decides whether a served console talks to S1 or to the fixtures. `npm run test:e2e` builds into
`dist-e2e/` and serves that, which leaves `dist/` deployable: nothing has to be rebuilt after a run,
and `install.sh --skip-build` cannot copy a test double onto a live origin. When checking any artifact
before serving it, grep for the baked value — `grep -c 'VITE_ENABLE_MOCK:"1"' dist/assets/index-*.js`
must read `0`.

## Build-memory note (Monaco)

`@monaco-editor/react` loads Monaco **on demand** (default CDN loader) rather than pulling the
~1k-module `monaco-editor` ESM graph into the build — bundling it alone OOM-kills constrained CI
boxes during `vite build`. The editor API is unchanged. To ship a fully offline image, build on a
host with ≥4 GB free RAM and switch `src/lib/monacoSetup.ts` to the bundled-monaco path (workers via
Vite `?worker`, `loader.config({ monaco })`).

## Container

`deploy/Dockerfile` builds the bundle and serves it with nginx. At start,
`deploy/docker/entrypoint.sh` writes `/config.js` from env (`FACTORY_API_BASE_URL`, …), so one image
targets any environment (UI-APP-005). nginx fronts `/api/v1` same-origin and disables buffering for
the SSE endpoint.

## Layout

```
src/
  api/        generated client + Zod, unwrap, errors, idempotency, queries, mutations, actions
  config/     runtime config loader
  i18n/       bilingual messages + i18next setup
  stores/     operator, session/csrf, language, ui, connection
  sse/        realtime refresh + ephemeral progress
  navigation/ the single source of truth for global and project navigation
  components/ app shell, top bar, grouped sidebar, page header, route tabs, stage stepper,
              help drawer, dialog, AllowedActionButton, form/notice/status primitives, toasts
  features/   setup, dashboard, approvals, projects, workspace (stages/gate/questions/manual/chat),
              inputs, review (artifact/metadata/graph/decisions/API/handoff), deployments,
              environments, governance, admin, project (members/grants/exemptions),
              change-requests, audit, notifications, settings, auth, secrets, help
  mocks/      MSW handlers + fake SSE
contract/     vendored S1 openapi.json (1.19.0, 147 paths)
```
