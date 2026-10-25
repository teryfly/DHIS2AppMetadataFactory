"""Data-classification policy engine (SRS 3.5).

Split by purity, which is what lets ``AI-NFR-01`` be enforced rather than hoped for:
``policy.engine`` and ``policy.model`` are pure functions over plain data,
``policy.resolution`` is the only place a classification is read from S0 or S2, and
``policy.orgpolicy`` is the only place one is written to ``ai``.

Every name this package publishes is a function, and no module inside it shares one of those
names: ``from factory_intelligence.policy import evaluate`` would otherwise hand out the
submodule to whoever imported it last, which is two objects answering to one name.  Hence
``selection`` and ``resolution`` behind ``select`` and ``resolve``, and ``refs`` behind
``judge_refs``.

This package re-exports its surface, unlike S3's other subpackages: the circular import
that forced ``output`` and ``agents`` to stay thin came from a leaf module importing a
sibling's *package* init, and every module here imports its siblings by full submodule
path, so initialising this package cannot re-enter a half-built module.
"""

from __future__ import annotations

from factory_intelligence.policy.engine import evaluate, highest_classification
from factory_intelligence.policy.model import (
    ContextItem,
    DeniedItem,
    EffectivePolicy,
    Verdict,
)
from factory_intelligence.policy.normalize import (
    normalize_environment_policies,
    normalize_one,
    org_policy_from_payload,
)
from factory_intelligence.policy.orgpolicy import effective_policy, read_org_policy, relax
from factory_intelligence.policy.resolution import resolve
from factory_intelligence.policy.selection import (
    CandidateModel,
    ModelDenial,
    Selection,
    select,
)

__all__ = [
    "ContextItem",
    "DeniedItem",
    "EffectivePolicy",
    "Verdict",
    "evaluate",
    "highest_classification",
    "normalize_environment_policies",
    "normalize_one",
    "org_policy_from_payload",
    "effective_policy",
    "read_org_policy",
    "relax",
    "resolve",
    "CandidateModel",
    "ModelDenial",
    "Selection",
    "select",
]
