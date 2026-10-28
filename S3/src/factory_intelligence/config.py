"""S3 configuration (00 doc 6.10; mirrors ``factory_blueprint.config``).

Runtime knobs only.  Anything a *deployment* must register — providers, models, per
agent model preference — lives in the ``ai`` schema registries (AI-REG-004) because it
has to be auditable and changeable without a redeploy; anything here is a *code*
default that changes the shape of the algorithms rather than the facts.

The two confidence defaults are the one place S3 must agree with S2 by construction:
S2's completeness rule blocks a low-confidence decision that carries no confirmation
record, so a lower S3 default invents questions a human need not answer and a higher
one lets a patch through that S2 will refuse at freeze.  ``DEFAULT_CONFIDENCE_FLOOR``
is asserted against S2's own constant in ``tests/unit/test_thresholds.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

#: Matches ``factory_blueprint.validators.completeness.DEFAULT_DECISION_THRESHOLD``.
DEFAULT_DECISION_CONFIDENCE = 0.7
DEFAULT_EXTRACTION_CONFIDENCE = 0.7


@dataclass(frozen=True)
class IntelligenceSettings:
    # --- confidence thresholds used when the Blueprint sets none (AI-INP-008) ----
    default_decision_confidence: float = DEFAULT_DECISION_CONFIDENCE
    default_extraction_confidence: float = DEFAULT_EXTRACTION_CONFIDENCE

    # --- retry budgets (AI-RT-011 / 013 / 021, see the plan's ruling 6) ---------
    #: transport-class retries after the first attempt: timeout, 429, 502, 503, 504.
    #: Never counted as a corrective retry.
    transport_retries: int = 5
    transport_backoff_base_seconds: float = 0.5
    transport_backoff_max_seconds: float = 20.0
    #: Total time one answer may spend waiting for the provider across those retries.  The
    #: count alone is not a ceiling when one attempt may take the full per-call timeout; with
    #: the 300 s default this is roughly five timed-out attempts plus their backoff.  A
    #: successful response resets it, so one slow chunk cannot spend the budget a later chunk
    #: needs.
    transport_wait_budget_seconds: float = 1800.0
    #: Corrective retries after the output ladder refuses an answer (AI-RT-011).
    #: Five re-emissions are allowed by default; the repaired answer is a complete envelope,
    #: not a delta, and every turn still counts against ``max_model_calls_per_run``.
    corrective_attempts: int = 5
    #: Corrective retries after S2 rejects a patch (AI-RT-013).  A malformed path is a
    #: mechanical mistake the model can fix when it is shown the exact operation and error,
    #: so the same five-attempt budget applies there.
    s2_reject_attempts: int = 5
    #: AI-RT-013: replenishing the UID pool is not a retry of anything
    uid_replenish_attempts: int = 2
    #: hard ceiling so a mis-set budget cannot loop a worker; AI-AC-01 relies on it.
    #: One initial answer plus five repair turns.
    max_model_calls_per_run: int = 6

    # --- context construction (AI-RT-004, AI-NFR-06) ----------------------------
    #: refuse rather than truncate (AI-POL-003)
    max_context_bytes: int = 1_500_000
    #: documents are fed to the model in chunks of this size (AI-NFR-06)
    document_chunk_chars: int = 12_000
    max_document_chunks: int = 40
    #: a 100 MB input file must complete (AI-NFR-06); larger is rejected outright
    max_input_bytes: int = 100 * 1024 * 1024

    # --- similar-object search (AI-AG-011) --------------------------------------
    #: normalised name similarity, 0..1, above which an object is "similar"
    similar_name_threshold: float = 0.85
    #: option-set Jaccard threshold for option sets and their values
    similar_option_threshold: float = 0.60
    #: how many similar candidates are put in front of the model per lookup
    similar_max_results: int = 8

    # --- provider / model call defaults (AI-LLM-003) ----------------------------
    #: Ceiling for one HTTP exchange with a registered model. 120 s was a guess about how long a
    #: model may take, and it was wrong in the direction that breaks the product: a bot that needs
    #: two minutes to answer a one-page requirement document produced
    #: ``model request timed out after 120.0s: ReadTimeout``, which a console cannot tell apart
    #: from a wrong address or a refused key. Overridable per deployment with
    #: ``FACTORY_LLM_MODEL_TIMEOUT_SECONDS`` (``bootstrap.initialize``).
    #:
    #: The number is a per-attempt budget, not a per-parse one: a timeout is retried under
    #: ``transport_wait_budget_seconds``, so a single answer cannot wait longer than that
    #: budget plus the attempt in flight.  Keep it under the stage's own deadline rather than
    #: raising it to "never fail".
    model_request_timeout_seconds: float = 300.0
    default_temperature: float = 0.0
    default_max_output_tokens: int = 8000

    # --- parse diagnosis log (AI-LLM-004 is the ledger; this is the transcript) ----
    #: How long an ``ai.llm_exchange`` row lives.  The log quotes the input it was built
    #: from, so it is kept only as long as it is useful for diagnosing a parse.
    llm_exchange_retention_days: int = 30
    #: One answer longer than this is stored truncated; a reasoning model can answer with
    #: tens of thousands of tokens and a log row must not grow without a bound.
    llm_exchange_response_limit_chars: int = 262_144

    # --- evaluation (AI-EVAL-001) ----------------------------------------------
    #: an eval set scores below this and the prompt/model may not be activated
    eval_pass_score: float = 0.95

    #: extra agent types this deployment has enabled beyond the milestone default
    enabled_agents: tuple[str, ...] = field(default_factory=tuple)


_DEFAULT = IntelligenceSettings()
_CURRENT = _DEFAULT


def settings() -> IntelligenceSettings:
    """The live configuration."""
    return _CURRENT


def configure(**changes: object) -> IntelligenceSettings:
    """Return the new settings after applying ``changes`` (process-wide)."""
    global _CURRENT
    _CURRENT = replace(_CURRENT, **changes)  # type: ignore[arg-type]
    return _CURRENT


def reset() -> IntelligenceSettings:
    """Restore the packaged defaults; used between tests."""
    global _CURRENT
    _CURRENT = _DEFAULT
    return _CURRENT


__all__ = [
    "IntelligenceSettings",
    "settings",
    "configure",
    "reset",
    "DEFAULT_DECISION_CONFIDENCE",
    "DEFAULT_EXTRACTION_CONFIDENCE",
]
