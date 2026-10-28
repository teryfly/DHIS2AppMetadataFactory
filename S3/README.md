# S3 — 需求与智能子系统 (`factory_intelligence`)

S3 turns uploaded material into requirement candidates, and turns requirement candidates into
Blueprint patches — by asking a language model, under a policy that decides what may leave the
Factory, through a validation ladder that decides what may be believed. It implements
[`requirements/S3_需求与智能_SRS.md`](../requirements/S3_需求与智能_SRS.md) and owns the `ai`
schema.

This README describes **package version 0.1.0**, the implementation behind SRS V1.3.0 and S1's
1.19.0 contract (147 paths). There is no CHANGELOG; behaviour below is taken from `api.py`, the
DTOs, the migrations and the tests.

It is a library, not a service: S1 (`factory_control`) imports
`factory_intelligence.api` and calls it in-process (D-13, D-09). There is no HTTP surface, no
worker and no database of its own beyond the `ai` schema.

## Install and test

```bash
cd S3
uv venv --python 3.12 .venv
uv pip install -e "../S0[dev]" -e "../S2[dev]" -e ".[dev]"

# one-time, against the Postgres container the other subsystems use.
# It has restart policy `no`, so a host reboot leaves it stopped, and a stopped container is what
# turns this suite into ~a dozen setup errors that have nothing to do with the code under test.
# Allow a few seconds after `start` before the first `exec` — the server is still initializing.
docker start factory-cp-pg
docker exec factory-cp-pg psql -U factory -d postgres \
  -c "CREATE DATABASE factory_test_s3"

FACTORY_TEST_DATABASE_URL=postgresql+psycopg://factory:factory@localhost:55432/factory_test_s3 \
  .venv/bin/python -m pytest -q

.venv/bin/python -m pytest -m perf -q          # AI-NFR-01; needs FACTORY_PERF=1 to actually measure
.venv/bin/python scripts/quality_gate.py       # AI-NFR-04 coverage floors
.venv/bin/mypy . && .venv/bin/ruff check src tests
```

**The test database must be exclusive to one session.** `testing/harness.py` migrates and, once
per session, **drops `core`, `bp` and `ai`**; two concurrent pytest sessions will fail each
other's migrations with `pg_type_typname_nsp_index` collisions and truncate each other's data.
Point a second session at a second database with `FACTORY_TEST_DATABASE_URL`.

No test reaches the network: the model is either the mock provider (AI-MOCK-001) or the
record-replay adapter (AI-LLM-006) with an injected transport that refuses to connect.

### The per-call model budget

`model_request_timeout_seconds` is the ceiling for one HTTP exchange with a registered model
(AI-LLM-003) — a generation, not a parse. A deployment sets it with
`FACTORY_LLM_MODEL_TIMEOUT_SECONDS=300` in the environment S1's service reads; the value is applied
once, in `bootstrap.initialize`, and a value that cannot mean seconds refuses to start the process
rather than quietly keeping the default.

It is a per-attempt budget: a transport failure is retried with exponential backoff
(`transport_retries`, 5 by default) until `transport_wait_budget_seconds` (30 minutes) of total
waiting is spent — whichever ceiling comes first. A successful response resets the wait budget, so
one slow chunk cannot starve the rest of a parse. Raise the timeout to cover a slow vendor, not to
make a dead one look alive. The health probe (`GET /models`, S5IF-GOV20, what the consoles' 测试连接
button calls) keeps its own fixed 5 s, so a two-minute model never makes the probe two minutes too.

The packaged default used to be 120 s. A Poe bot that needs two minutes to answer a one-page
requirement document then answered `model request timed out after 120.0s: ReadTimeout`, which no
screen can distinguish from a wrong address or a refused key — the number was a guess about vendor
latency in the one direction that breaks the product.

### Where the `ai` schema comes from

`migrations/versions/0001_ai_initial.py` creates the tables from the **ORM metadata** rather
than from hand-written DDL — the same way S2 does it, so a column added to a model cannot be
missing from the database. The one hand-written statement is the `ai_model_call_guard`
trigger, because a usage row is evidence (AI-NFR-03) and must not be editable after the fact.
`0002_ai_read_and_model_endpoint.py` is the first change on top of that: `ai.nrr_entry`,
`ai.input_parse.mapping_proposal`/`result` and `ai.agent_run.last_progress` (the read
projections AI-INP-013/014/015 and AI-RT-019 need) and the per-model `endpoint` /
`api_key_secret_ref` of AI-LLM-003. `0003_ai_provider_preset.py` adds `vendor` and
`suggested_models` (console labels, not capabilities). `0004_ai_llm_exchange.py` adds
`ai.llm_exchange` (AI-INP-016). Every statement there is guarded by an inspection of what
the database actually holds, because `0001` runs `create_all(checkfirst)` and a fresh
deployment therefore arrives with all of it already.
`create_all` is `checkfirst`, so a database that already has `ai` keeps what it has.

### Compatibility with S1, checked from S1's side

Two different runs prove the handshake, and they report different totals — writing one command for
both is why the number below could not be reproduced:

```bash
cd S1 && uv pip install --python .venv/bin/python -e ../S3

# A — S1's suite, which is where the in-process calls into real S3 live
#     (tests/service/test_real_s3.py skips unless factory_intelligence is importable).
FACTORY_TEST_DATABASE_URL=postgresql+psycopg://factory:factory@localhost:55432/<a database of your own> \
  FACTORY_PROVIDER_MODE=real .venv/bin/python -m pytest -q

# B — S3's suite, run in S1's interpreter, to show S3 still passes next to S1's dependency set.
FACTORY_TEST_DATABASE_URL=postgresql+psycopg://factory:factory@localhost:55432/<another one> \
  FACTORY_PROVIDER_MODE=real .venv/bin/python -m pytest -q ../S3/tests
```

The `../S3/tests` argument is what makes B a different run: S1's `testpaths = ["tests"]` means a
bare `pytest -q` here collects S1's tests only. Install is what makes the real-S3 tests in A run at
all — `has_real_provider` keys on importability, not on `FACTORY_PROVIDER_MODE`.

The harness drops and re-creates schemas in the database the URL names, and under
`FACTORY_PROVIDER_MODE=real` the other subsystems' schemas live in that same database — so two
sessions pointed at one database corrupt each other, and the failures that produces look
exactly like real ones. Give each run its own database, as S3's own suite must.

Measured on 2026-10-22: **run A — 617 passed, 1 skipped** (12 min 14 s); **run B — 1694 passed,
5 skipped, none failed** (4 min 39 s), which is exactly what S3's suite reports in its own venv on
the same host — that equality is the point of B. The 5 skips are the opt-in performance smoke tests
(`FACTORY_PERF=1`). Those numbers are a historical measurement, not a promise about the current
tree: they were taken before S1's 1.19.0 questionnaire/`language` changes, and run A moves with
S1's suite. Run B was 1650 passed / 5 skipped when last measured on 2026-10-03 against the SRS
V1.1.x implementation, and 288 at V1.0.0; the growth is the result-shape tests this section was
warning about, now written. Run A was 482 passed / 1 skipped against S1's 1.6.0 contract; S1's
README documents how to run its suite, not what it returns.
The parts of the handshake that matter are pinned in `tests/unit/test_contract.py`: the original
ten names `IntelligenceAdapter.METHOD_MAP` started with exist on real S3 and are callable, the
external-I/O members are marked, every result field S1's DTO mirror reads is present under exactly
that name, and every result is round-tripped through
`mirror.model_validate(value.model_dump())` against a mirror built the way S1 builds its own —
because the mirror's `extra="ignore"` would otherwise drop a rename or a re-typed value
without a sound.

S1's `METHOD_MAP` now resolves **38** names: the original ten plus one per governance capability
(`update_provider`/`update_model`/`check_provider_health`/`list_regressions` included), the three
parse-log reads (`list_llm_exchanges`/`get_llm_exchange`/`purge_llm_exchanges`), and
`get_chat`/`clear_chat`. Beyond those, `factory_intelligence.api` exports one typed function per
governance capability (AI-GOV-001) and the three parse reads (AI-INP-013/014/015). S1 may keep
forwarding `governance(action, payload)` — it answers from those same functions, and
`test_srs_v11_additions.py::test_the_facade_and_the_typed_function_answer_one_row` holds the two
equal — but an endpoint that must declare a named request and response component (00 doc 6.11)
should call the function. The façade is deprecated (`DEPRECATED_SINCE = "1.1"`,
`REMOVE_AT_MILESTONE = "M2"`) and is still present because the milestone rank has not advanced.

## The fixed contract

`S1/src/factory_control/adapters/registry.py:198` resolves the names on
`factory_intelligence.api` (the original nine of SRS 7, plus `list_prompt_activations` →
`list_activations`, S1's own addition for S5IF-GOV10, and the governance verbs added through
contract 1.15.0–1.19.0), and
`S1/src/factory_control/adapters/delegating.py` forwards
`*args, **kwargs` verbatim and coerces every result with
`dto.model_validate(value.model_dump())`. That makes the *field names* of
`AgentRunResult`, `ParsedInput`, `Question`, `Answer` and `Issue` part of the contract, and
S1's DTO mirrors use `extra="ignore"` — so a renamed field would vanish silently rather than
fail. `tests/unit/test_contract.py` pins them against S1's own lists, and
`tests/unit/test_architecture.py` pins which functions are marked external I/O.

Three call-shape details worth knowing (the SRS table spells them out, and `api.py`'s module
docstring records why they differ from the older chapter-7 sketch): `explain_issues` and
`run_evaluation` take keyword-only `idempotency_key` / `progress` / `cancel` (00 doc 6.3, 6.4 —
both write artifacts), `delete_project_data` takes `session` because it must enlist in S1's live
transaction, and `run_evaluation`'s `prompt_version` is the qualified spelling AI-REG-001 uses
(`mapping.decide.v3`), split at the boundary rather than letting a dotted string into every
registry lookup.

## Module map

| Path | Responsibility | Requirement group |
| --- | --- | --- |
| `api.py` | the only importable surface, including one typed function per governance capability | AI-API-001, AI-GOV-001 |
| `parsing/` | input → NRR: dispatch, deterministic parsers, model-backed extraction, merge, confidence | AI-INP-001…012 |
| `parsing/records.py` | the parse-record, column-mapping-proposal and NRR queries over `ai` | AI-INP-013/014/015 |
| `context/` | the prompt as built: order, fencing, retrieval, similar objects, byte budget, per-turn language | AI-RT-003…009, AI-RT-025 |
| `policy/` | the effective matrix (set intersection and ceiling expansion), per-model selection, organisation policy and relaxations | AI-POL-001…008 |
| `llm/` | provider protocol, OpenAI-compatible adapter (one wire protocol), health probe, record/replay, mock agent, token maths | AI-LLM-001…007, AI-CAT-001, AI-MOCK-001 |
| `prompts/` | versioned prompt sources with TOML front matter, the registry, the activation gate, reload | AI-REG-001…003, AI-REG-006 |
| `agents/` | the agent table (SRS 3.3 as data) and the deterministic constraint checks | AI-AG-001…014, AI-RT-012 |
| `output/` | the four-rung validation ladder, drawing item schemas from S2's own section schema | AI-RT-010…013, AI-ASM-03 |
| `runtime/` | start / resume / cancel / get, patch submission, questions (`SINGLE`/`MULTIPLE`, round ids), context store, progress, retry accounting, explain | AI-RT-001…032 |
| `runtime/chat.py` | in-stage dialogue: context, policy, one turn, transcript, clear/history | AI-CHAT-001…004 |
| `eval/` | evaluation sets, the runner and the scoring, with injection cases counted apart | AI-EVAL-001…004 |
| `cost/` | the usage ledger and rollup, the budget state machine and write switch, the parse-time exchange log | AI-COST-001…003, AI-INP-016, AI-NFR-03 |
| `governance/capabilities.py` | every governance capability as one typed function, including provider/model diff edits, probe, regressions and agent catalogue | AI-REG-004/006, AI-POL-002/006, AI-COST, AI-GOV-001…011 |
| `governance/dispatcher.py` | the deprecated `governance(action, payload)` façade as a shim over those functions | CP-API-007, AI-GOV-009 |
| `cleanup.py` | project data deletion, keeping the usage ledger and governance rows | AI-DEL-001 |
| `models/` | the `ai` schema as ORM tables (`DELETABLE_TABLES` / `RETAINED_TABLES` decide cleanup) | AI-DEL-001, AI-NFR-03, AI-INP-015 |
| `migrations/versions/` | `0001_ai_initial` … `0004_ai_llm_exchange`; 0001 builds from ORM metadata and adds the `ai_model_call_guard` trigger | AI-DATA-01 |
| `s2/` | `BlueprintPort` over `factory_blueprint.api` — drafts, patches, section schemas, project semantics; thin by design | AI-ASM-03, AI-RT-013, D-14 |
| `dto/` | the request and result types whose field names are contractual (`answerMode`, `recommended`, `suggestionIds`, `language`, exchange views) | AI-API-001, AI-RT-023/025 |
| `testing/` | `FakeIntelligenceApi` (00 doc 6.1) and the database harness | AI-API-001 |

## Provider and model governance

Every capability is a typed function (AI-GOV-001); S1's `PATCH /governance/providers/{name}` and
`PATCH /governance/models/{ref}` call `update_provider` / `update_model`, not `register_*`.

**Editing is not re-registering.** `register_provider` / `register_model` write every column, so
an omitted `endpoint` or `apiKeySecretRef` is written as `NULL` — which silently clears the
per-model override AI-LLM-003 allows and lets S1's read path back-fill the provider's address.
The update verbs therefore assign **only the columns the caller named**:

* `ModelPatch` is all-optional. An absent price, address, Key reference or `enabled` keeps its
  stored value. `capabilities` is a bare mapping, not `CapabilitiesView`, because a typed view
  cannot tell "the caller did not name this key" from "the caller named its own default"; it is
  **merged key by key** against the stored JSON, so raising `maxContextTokens` leaves
  `structuredOutput` and `vision` alone. `applicableAgents` is replaced whole — `{}` means
  "nobody" and is a value, not an absence. An empty patch is refused (an untouched row answering
  200 reads like a successful edit). A blank endpoint/Key reference is refused rather than
  stored. There is no way to *remove* an override: clearing a model-level address means
  re-registering the model on the provider whose address you want.
* `ProviderPatch` behaves the same for class, endpoint, Key reference, `adapter`, `enabled`,
  `vendor` and `suggestedModels`; `suggestedModels=[]` clears the candidates, `null` leaves them.
* Both audits reuse `ai.model.registered` with `before` / `after` and
  `action="updateProvider"` / `"updateModel"`; a registration carries no `before`.
* `set_provider_enabled` / `set_model_enabled` only flip `enabled` and audit
  (`action="setEnabled"`). Disabling keeps the row, its price and its evaluation history — the
  usage ledger and evaluation evidence must survive the decision.

**The health probe** is `check_provider_health(name)` (S5IF-GOV20): the adapter's `GET
{base}/models` on a fixed five-second budget, no generation, so nothing is billable and no
project content leaves. Every failure is an *answer*, not an exception, because the operator is
asking "why could the stage not get a reply": a non-retryable HTTP refusal (401/403) means the
address is alive; a timeout, connect failure or retryable status (408/409/425/429/500/502/503/504)
is unreachable; a Key reference that resolves to nothing and an address nobody filled in are two
more distinct `detail` sentences. The view has no Key field at all. Two edges are worth knowing:
an **unregistered** provider raises `NOT_FOUND` (S1's fake and 1.16.0 test expect a 200 with
`healthy=false` — an adapter-side translation is still missing), and when a reference cannot be
resolved the adapter's sentence **names the reference** (never its value); if S1's "no key
reference echoed" rule is read literally, `detail` needs trimming there.

**Secrets.** `api_key_secret_ref` is a reference or nothing: `env://NAME` is resolved by S0's
environment backend, `db://name` by the backend S1 installs at bootstrap when
`FACTORY_CREDENTIAL_MASTER_KEY` is at least 32 characters (HKDF-SHA256 + AES-256-GCM, the record
name as additional authenticated data). Without a long-enough key the `db://` backend is not
installed: `env://` still works, `GET /setup` reports `credentials.masterKeyConfigured=false`,
and credential writes answer 503. A row sealed under another master key fails as `keyMismatch`;
nothing is retried against a second key. S3 never reads the environment or the credential table
itself — it calls `resolve_secret` and drops the value into one request frame. Reads return the
reference only (`null` when unset), never a value, mask, length or prefix.

**One wire protocol.** `openai_compat` is the only implementation: `POST {base}/chat/completions`
with `Authorization: Bearer <key>` and a `GET {base}/models` probe. `provider_for()` ignores the
stored `adapter` value, so the column is display-only and must not become a console choice.
S1's provider catalogue (`GET /governance/provider-catalogue`, AI-CAT-001) is therefore a list of
OpenAI-compatible addresses with `wireProtocol="openai_compat"`, suggested model names,
`keyRefSuggestion` (empty for local servers) and a `local` flag — suggestions, not verified
capabilities.

## In-stage dialogue

`stage_chat` is delivered (SRS 12 scheduled it for M3): one synchronous model call per turn,
answered by the Review Assistant or, when a patch is on the table, by the stage's own delivered
agent (REQUIREMENTS → `REQUIREMENTS`; DOMAIN → `DOMAIN`; MAPPING → `MAPPING`; PAGE_DESIGN →
`API`/`UI`). It is bounded by:

* **Stage** — only `DesignStage` values have a dialogue; anything else is `NOT_FOUND`.
* **Writing** — a patch is built and submitted to S2 only when a draft is open, the stage's
  agent is delivered and the mapping is not frozen. Otherwise the model's patch is dropped
  *before* validation and the answer says "advice only"; a frozen mapping still gets an answer
  about the reasoning. S2 refusing a patch is part of the answer (`patchIds` empty, `answer`
  names the rejection), not a 500.
* **Governance** — an unactivated prompt raises `AI_PROMPT_NOT_ACTIVATED` before any call; no
  model permitted by the effective policy raises `AI_PROVIDER_UNAVAILABLE`; the context is
  byte-bounded and never truncated.
* **Idempotency** — one paid turn per key; a repeat replays the stored answer.
* **Language** — `language` is taken per turn, folded into the rebuilt system message, so a
  resumed or next turn can switch language without rewriting the transcript.

History is an `AGENT_OUTPUT` artifact per `(project, stage)`, appended as a new version;
`clear_chat` flips `ai.chat_session.cleared` and starts a new version rather than deleting
anything. `get_chat` returns the `Transcript` dataclass (`project_id`, `stage`, `session_id`,
`cleared`, `context_artifact_id`, `turns`, `classification`), and its `turns` are
`{role, content, at}` for the user and `{role, content, patchIds, questions, agentType,
promptVersion, at}` for the assistant. Reading it touches object storage, hence external I/O.

## A questionnaire, one resume

A `Question` now carries `answerMode` (`SINGLE`/`MULTIPLE`; unknown falls back to `SINGLE`) and
each `Suggestion` carries `recommended`. A user-facing console renders a radio group or a
checkbox group from that one field; several suggestions may be recommended for a `MULTIPLE`
question because each is useful for a different reason.

`Answer` accepts both shapes: `suggestionId` (single, and the historical confirmation record) and
`suggestionIds` + `suggestionTexts` (multi). The resume path feeds every selected **text** to the
model, with `customText` appended as the person's own addition; the ids alone would make the
model guess. S1 submits the whole stage questionnaire in one call and resumes **at most once**;
S3 applies all answers in one turn, and if a blocking question is still unanswered it returns
`WAITING_INPUT` without calling a model, naming the open local ids in `denialReasons`.

Local ids are S3's, not the model's. A second question round uses an attempt prefix (`Q2-1`,
`Q2-2`…) instead of restarting at `Q1`, because S1 dedupes on `(agentRunId, sourceLocalId)` and
*silently skips* a repeat — a new round starting at `Q1` would lose every question it asked.
`affects` is rewritten to the canonical ids so an answer and a confirmation record address the
same question.

## The parse-time LLM log

`ai.model_call` is the billing ledger and carries no prompt or answer. Diagnosing a parse needs
the opposite, so `ai.llm_exchange` stores what a **business-input parse** sent and received —
including every transport retry. Only calls whose `CallContext.input_artifact_id` is set are
logged; agent runs, evaluations and chat turns stay out.

* `list_llm_exchanges(project_id, input_artifact_id, limit, offset)` returns summaries clocked by
  `created_at`, `id`, oldest first, `limit` clamped to 200. `get_llm_exchange(...)` returns the
  request messages, JSON schema, temperature, max output tokens, the agent hint, the raw answer,
  finish reason, truncation and usage; a row that does not belong to that project **and** input
  returns `None`.
* Status is `IN_FLIGHT` / `OK` / `ERROR`; an `IN_FLIGHT` row older than the request timeout plus
  the transport wait budget is projected as `INTERRUPTED` at read time, never stored.
* Images are metadata only (`mediaType`, `label`, `contentHash`, `sizeBytes`); base64 never enters
  the log. Answers are cut at `llm_exchange_response_limit_chars` (262 144) and marked
  `storageTruncated`.
* `llm_exchange_retention_days` (30) sets `expiresAt`; `purge_llm_exchanges()` deletes expired
  rows and returns the count (S1's daily job calls it). `delete_project_data` removes the
  project's transcripts with the rest of its rows.

## Test doubles

`testing/fakes.py::FakeIntelligenceApi` implements every public function in memory and returns
S3's real DTOs, so a field-name or type error in S1's coercion fails in S1's own suite. It
simulates idempotency (a repeated start key replays), rejects keywords the real function does not
accept, and raises for gated capabilities exactly as `api.py` does. `stage_chat`, `clear_chat`,
`set_budget`, the parse-log reads and the governance verbs answer for real; `desensitize` raises
`FeatureNotEnabled`. `testing/harness.py::IntelligenceHarness` migrates the database, drops
`core`/`bp`/`ai` once per session, and must be pointed at a database no other session uses. The
model side is either `llm/mock_agent.py` (scripted envelopes per agent) or
`llm/record_replay.py` with an injected transport that refuses to connect; no test reaches the
network.

## Nine decisions worth knowing before you change anything

1. **The transaction alternation.** A run is `T1` (idempotency, budget, the `RUNNING` row) →
   `G1` (every read, the model call, the ladder, artifact staging — no transaction open) →
   `T2` (patch, artifact, rows, audit and `idempotency.complete` in one commit) → `G2` (only
   for `UID_POOL_EXCEEDED`). The model call is therefore in the gap *by construction*, enforced
   by an AST assertion in `tests/unit/test_architecture.py`. The one deliberate exception is the
   per-call usage row, committed apart and immediately, so a failed run still shows what it paid
   for (AI-NFR-03).
2. **Every model call is booked, at its own call site.** `ai.model_call` is the ledger AI-NFR-03
   measures as a percentage over *calls*, so the two paths that reach a provider without a run
   wrap it in `cost.recording.RecordingProvider`: an extraction books
   `call_role="extraction"` under `workflow_id="parse:<artifact>"`, and an evaluation books
   `call_role="evaluation"` under `workflow_id="eval:<run>"` with **no project** — the column is
   nullable for exactly that case, since an evaluation spends no project's budget, and every
   project rollup and budget sum filters on it. The evaluation also folds the same figures into
   `ai.eval_run.summary` (`modelCalls`, `inputTokens`, `outputTokens`, `estimatedCost`) beside
   the score they produced.
3. **S3 calls `prepare_draft_base` itself.** S1 has no port method for it and never calls it, so
   an agent that reached `submit_patch` first would die on `BP_DRAFT_NOT_MATERIALIZED`.
4. **A waiting turn submits no patch.** AI-RT-016 puts the confirmation record in the *later*
   patch, and the decision a blocking question gates is the one that patch would encode.
   Non-blocking questions do not stall the stage. `HIGH` is always blocking.
5. **Policy is set intersection.** `effective = org ∩ every environment`, per classification
   level, computed as a pure function with no I/O — which is what makes AI-NFR-01's latency
   budget structural rather than measured. PHI_PII is unrelaxable three times over: the empty
   set absorbs the intersection, `relax` refuses the request, and the context builder never
   emits the content. A **ceiling** is expanded, not matched: `llmDataPolicy` (what S4's
   environment protection ships, forwarded verbatim by S1) and `maxProviderClass` name how far
   data may go, so they allow that class *and every stricter one* — reading a ceiling as one exact
   class makes a wider ceiling deny a local model. The parser must recognise S4's spelling: when
   it did not, every environment whatever it was configured as collapsed to the `LOCAL_ONLY`
   fail-safe, which is why the seam is pinned from S1's side as well as here
   (`S1/tests/service/test_real_s4_deletion.py`, since S3 may not import S4).
6. **UIDs are S2's alone.** S3 never mints, allocates or writes one. The ban is structural: only
   the path token `uid` is refused (so S2's own `observedUid` brownfield field survives), and
   `changesUid` answers are handled by removing and re-adding the entry, leaving S2 to allocate.
7. **The run query answers in its own vocabulary.** `start_agent_run` and `resume_agent_run`
   return one of AI-RT-017's six results; `get_agent_run` returns those *plus* `RUNNING` with
   the last progress event, the question local ids, the per-model denial reasons and the
   rejected artifacts (AI-RT-019, AI-AC-14). S1 no longer has to derive `RUNNING` from the
   stage, and Question text still never comes back from S3 — those rows are S1's.
8. **A query never opens an artifact.** AI-INP-013/014/015 are `只读` functions that S1 calls
   inside its own transaction, and reading artifact content is external I/O (00 doc 6.2) — so
   the parse projects what those reads need into `ai.nrr_entry` and
   `ai.input_parse.mapping_proposal` as it writes its record. The NRR artifact stays the
   authoritative document; the rows are its index, deleted with the project (AI-INP-015 puts
   category and parsing detail in the NRR artifact *and* the `ai` schema, never in S2's closed
   `requirements` section).
9. **A finding has one spelling.** Anything S3 reports as a validation problem is S2's Issue
   with `level` ∈ `BLOCKING`/`WARNING`/`INFO` (AI-RT-018, 00 doc 6.10); `severity` is accepted
   inbound for older callers and never emitted. `description`/`object_key`/`suggested_action`
   are computed from `message`/`business_key`/`remediation`, because S1's mirror declares those
   names with `extra="ignore"` and would otherwise lose the text.

## Acceptance criteria → tests

| Criterion | Where it is proven |
| --- | --- |
| AC-POL-01, AC-AG-01 (S3 part) | `tests/integration/test_agent_run_guards.py`, `tests/unit/test_policy_select.py`, `tests/integration/test_org_policy.py` |
| AC-POL-02, AI-POL-005 | `test_agent_run_guards.py` (PHI input never reaches the provider), `tests/unit/test_policy_effective.py` |
| AC-REQ-01 (deterministic parse, twice identical) | `tests/unit/test_parsers_*.py`, `tests/unit/test_parse_service.py`, `tests/integration/test_parse_input.py` |
| AC-REQ-02 (low-confidence extraction becomes a Question) | `tests/integration/test_parse_input.py`, `tests/unit/test_constraint_checks.py` |
| AI-AC-01 (two invalid answers → FAILED, exactly two calls) | `test_agent_run_guards.py::test_two_invalid_answers_fail_the_run_after_exactly_two_calls` |
| AI-AC-02 (UID in a CREATE entry → no patch) | `test_agent_run_guards.py`, `tests/unit/test_constraint_checks.py` |
| AI-AC-03 (injection attempts) | `tests/integration/test_evaluation.py` (injection cases counted apart), `tests/unit/test_output_ladder.py`, `src/factory_intelligence/context/fence.py` |
| AI-AC-04 (resume in the same context, with a confirmation S2 accepts) | `tests/integration/test_agent_runtime.py::test_resuming_confirms_the_gated_decision_in_s2_itself` |
| AI-AC-05 (same key → same run, no second call) | `test_agent_run_guards.py`, `test_agent_runtime.py` |
| AI-AC-06 (environment policy narrows the answer) | `tests/integration/test_org_policy.py`, `tests/unit/test_policy_effective.py` |
| AI-AC-07 (no evaluation → no activation) | `tests/integration/test_prompt_activation.py` |
| AI-AC-08 (cancel at the next safe point) | `test_agent_run_guards.py::test_a_cancel_flag_set_by_another_worker_stops_at_the_next_safe_point` |
| AI-AC-09 (over budget) | `test_agent_run_guards.py`; `test_governance.py::test_setting_a_budget_is_the_switch_the_runtime_rule_already_waited_for` |
| AI-AC-10 (record-replay without a network) | `tests/unit/test_llm_record_replay.py` |
| AI-NFR-01 (policy P95 < 50 ms at 100 items) | `tests/perf/test_policy_latency.py` (`FACTORY_PERF=1`) |
| AI-NFR-03 (100 % of calls in the ledger) | `test_agent_runtime.py::test_the_run_writes_one_usage_row_per_call_even_for_a_run_that_fails` (a run), `test_parse_input.py::test_a_model_assisted_parse_books_its_call_in_the_usage_ledger` (an extraction), `test_evaluation.py::test_an_evaluation_scores_the_version_and_records_the_evidence` (an evaluation), `tests/unit/test_usage_recording.py` |
| AI-NFR-04 (coverage floors) | `scripts/quality_gate.py` |
| AI-API-001 (the Fake) | `tests/unit/test_fakes.py` |
| AI-INP-013/014/015, AI-AC-15 | `tests/integration/test_srs_v11_additions.py` (parse records, the column-mapping table, the filtered and paged NRR query) |
| AI-INP-012 (same input, same options) | `test_srs_v11_additions.py::test_the_same_input_with_the_same_options_returns_the_first_result` |
| AI-INP-009 across inputs | `test_srs_v11_additions.py::test_two_inputs_that_disagree_produce_one_conflict_question` |
| AI-INP-001 (mapping agents read NRR only) | `test_srs_v11_additions.py::test_a_mapping_agent_is_refused_a_raw_business_input` |
| AI-INP-002 (reuse intent is reviewed) | `test_srs_v11_additions.py::test_existing_metadata_is_reported_as_a_reuse_candidate_not_a_fact` |
| AI-AG-013 (a page's UI waits for its API) | `test_srs_v11_additions.py::test_the_ui_agent_of_a_page_waits_for_that_page_s_api_agent` |
| AI-AC-13 (provider reads carry only the Key reference) | `test_governance.py::test_provider_register_answers_the_row_and_audits_the_registration`, `test_governance.py::test_a_provider_probe_answers_why_it_is_unreachable` |
| AI-AC-14, AI-RT-019 (`RUNNING` + progress) | `test_srs_v11_additions.py::test_a_run_in_flight_is_reported_as_running_with_its_progress` |
| AI-AC-16, AI-RT-018 (`level`) | `test_srs_v11_additions.py::test_a_refused_run_reports_its_findings_as_issues_with_a_level` |
| AI-GOV-001…011 (typed governance, façade parity) | `test_srs_v11_additions.py`, `tests/integration/test_governance.py` |
| AI-AC-12, AI-GOV-003 (`details` names the missing gate) | `test_srs_v11_additions.py::test_an_activation_refusal_names_what_is_missing` |
| AI-AC-11, AI-GOV-005 | `tests/integration/test_cost.py` (the refusal is a `VALIDATION_ERROR` with the allowed dimensions) |
| AI-RT-001 (input and Blueprint hashes) | `test_srs_v11_additions.py::test_a_run_records_the_hash_of_every_input_it_read` |
| AI-BR-08 (no reasoning is persisted) | `test_srs_v11_additions.py::test_a_models_reasoning_is_never_recorded_as_a_decision` |
| AI-AC-08 at the post-call safe point | `test_srs_v11_additions.py::test_a_flag_raised_while_the_model_answers_stops_before_the_patch` |
| AI-AC-17, AI-REG-006/010 (only named columns; capabilities merge; before/after) | `test_governance.py::test_editing_a_model_changes_only_the_fields_the_caller_named`, `...::test_a_capability_edit_that_names_one_key_leaves_the_other_two_as_they_were`, `...::test_an_edit_records_the_previous_values_alongside_the_new_ones` |
| AI-AC-18, AI-GOV-011, AI-LLM-005 (probe answers, no key field) | `test_governance.py::test_a_provider_probe_answers_why_it_is_unreachable`, `tests/unit/test_llm_openai_compat.py` |
| AI-AC-19, AI-RT-023/024 (`answerMode`, `recommended`, round ids) | `tests/unit/test_agent_outputs_dto.py::test_answer_mode_and_recommended_survive_into_the_canonical_question`, `...::test_an_unknown_answer_mode_falls_back_to_single`, `...::test_a_later_round_gets_ids_that_cannot_collide_with_the_first` |
| AI-AC-20, AI-CHAT-002/004 (frozen mapping, transcript) | `tests/integration/test_stage_chat.py` |
| AI-AC-21, AI-INP-016 (list/get/purge, scoped) | `tests/integration/test_llm_exchange.py` |
| AI-AC-22, AI-COST-001/002 (budget write and rollup) | `test_governance.py::test_setting_a_budget_is_the_switch_the_runtime_rule_already_waited_for`, `...::test_usage_rollup_answers_the_grouping_it_was_asked_for` |

## Milestones

M1 and M2 are implemented. Four capabilities SRS 12 schedules later are delivered **without
moving `CURRENT_MILESTONE`** (still `M2`), because the rank also gates the source extractors:

* `feature.DELIVERED_CAPABILITIES` names `stage_chat`, `clear_chat` and `set_budget` (and
  `get_chat`, which does not need a gate). In-stage dialogue is therefore live, and
  `set_budget` is the write switch for a rule the MVP already enforced at run start.
* `feature.DELIVERED_AGENTS` names `REVIEW`, `API`, `UI` and `FIXTURE`. Their prompt sources,
  evaluation sets, constraint rules and S2 section schemas all ship, so PAGE_DESIGN is served
  end to end. Their `milestone` field still says `M3`: that is SRS 12's schedule, while
  `list_agents().enabled` is what this build actually answers.

What is genuinely **not** delivered in 0.1.0:

* **source-code and OpenAPI extraction** — `ParserType.SOURCE_CODE` / `OPENAPI` are registered
  and the parse request is refused with `AI_FEATURE_NOT_ENABLED` by `require_milestone(M3)`.
* **`desensitize`** — a public function, but it raises `FeatureNotEnabled`
  (`capability=desensitize`, `milestone=M4`). Its table was created in migration 0001, so
  enabling it adds no schema and no API.
* **the extraction prompt's evaluation set** (AI-INP-011) — see "Deliberately not in this build".

An undelivered **agent** is reported differently on purpose: `start_agent_run` returns
`status=FAILED` with `run_id=None` and a reason naming the milestone, because S1 turns that into
a visible stage outcome and keeps the Gate's manual-authoring path open, whereas an exception
would fail the whole Temporal activity. A stage whose agent is served but whose prompt has no
active version is refused the same way (`AI_PROMPT_NOT_ACTIVATED`), not run on a placeholder.

## Gaps owned by other subsystems

Found while building against the real interfaces; each is S1's or S2's to close, and S3 works
around none of them silently.

* **S1's `Issue` and parse mirrors still carry two legacy spellings.** `ports/dto.py` declares
  `description`/`object_key`/`suggested_action` and a `severity`→`level` shim, and
  `rest/schemas/common.py` keeps `severity` as an excluded alias. S3 now emits the canonical
  `level`/`message`/`business_key`/`location` (and the legacy names as computed duplicates), so
  the shims can go; they are harmless until then.
* **S1's `get_agent_run` correction is now dead code.** `services/agent_runs.py:88-95` derives
  `RUNNING` from the stage because S3 used to report a running run as `WAITING_INPUT`. S3
  reports `RUNNING` itself now; the correction passes it through unchanged and can be deleted.
* **The probe's 404 does not match S1's 200 expectation.** `check_provider_health("never-registered")`
  raises `NOT_FOUND`; S1's fake and `test_s5if_endpoints.py` expect a 200 with `healthy=false`
  (`S1 CHANGELOG 1.16.0` says "an unregistered provider is an answer, not an error"). No adapter
  translation exists in `IntelligenceAdapter`, so a real deployment answers 404. S1 owns the
  choice: catch and project, or change the test and the changelog.
* **The probe's `detail` can name a Key reference.** `ProviderHealthView` has no key field, but a
  `db://`/`env://` reference that cannot be resolved produces `detail="the API key reference
  env://… could not be resolved"` — the reference name, never the value. S1's 1.16.0 note says
  the probe does not echo the key reference; if that is literal, trim `detail` at the REST layer
  (S3's own test only asserts that the successful answer carries no `apiKeySecretRef`).
* **S1 never requests the API agent** (`_agent_type_for` maps PAGE_DESIGN to `UI`), so
  AI-AG-013's ordering can be satisfied by S3 (it now enforces the order from its own run
  rows) but is not reachable from a stage run until S1 asks for `API` first.
* **S1's `parse_input` call hard-codes the idempotency key `parse:{artifact_id}`** and its API
  layer never passes `options`. S3 now also replays by *content* (same artifact, same options),
  so the confirmed column mapping takes effect either way, but a distinct key per attempt is
  still what AI-INP-006's second half assumes.
* **`ParsedInput.used_model` is a bool; S1's mirror declares `str | None`.** S1's
  `DelegatingAdapter._coerce` revalidates every result, so a real parse result fails there
  (S3's own Fake never sets the field, which is why S1's suite is green). `AI-INP-004`/`§5`
  spell the fact as a boolean, so S1's mirror is the side to change. The same class of
  mismatch on `Suggestion.regenerates` (AI-RT-010 spelled it a bool; S5IF-FLD08 wants a
  list of artifact references) is now resolved toward the contract: the field is
  `list[str]`, and both S3's validator and S1's question view coerce the bool that older
  prompts and existing `cp.questions` rows still carry.
* **S1 has no `prepare_draft_base` port method**, so its own manual-patch path cannot work
  against a real S2 either. S3 calls it before its own runs.
* **S2's patch applier addresses array members by index**, while its read model reports entries
  by `businessKey` (`/mapping/entries/<key>`), and no public S2 call resolves one to the other.
  S3 therefore folds a confirmation into the entry its own patch creates and refuses — with a
  message naming the reason — a confirmation for an entry it did not add.

## Deliberately not in this build

* **AI-INP-011's evaluation set for LLM extraction.** Every *agent* has one (AI-EVAL-001, and
  `run_evaluation` scores it), but the document and image extraction prompt lives in
  `parsing/documents/llm_extract.py` with its own `promptHash`, outside the Prompt Registry.
  A before/after comparison for it needs the prompt registered as a version first — which is
  AI-REG-001's own rule ("提示词禁止散落在代码里") — and that move is a design change for the
  extraction path, not a patch. Until then a change to that prompt is reviewed by its hash and
  by the parse tests, not by a score.
