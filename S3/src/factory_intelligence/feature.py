"""Milestone gating (SRS 12).

SRS 12 schedules the API / UI / Fixture agents and in-stage chat for M3, and
desensitisation and budget enforcement for M4.  The functions they belong to are all
declared in SRS 7 and in S1's adapter, so they must *exist* and be callable; what they
must not do is pretend to have produced a result.  Hence a raised
:class:`FeatureNotEnabled` rather than an empty success — the same resolution S2 chose
for its undelivered compilers (``BP_FEATURE_NOT_ENABLED``).

One exception, deliberate: :func:`agent_gate_result` exists because an *agent run* has
a result channel, and S1 turns ``POLICY_DENIED``/``FAILED`` into a visible stage
outcome while an exception fails the whole Temporal activity.  See the plan's ruling 2.

The milestone is *not* advanced to say that in-stage chat and the budget switch exist:
:data:`DELIVERED_CAPABILITIES` names them one by one.  ``CURRENT_MILESTONE`` is a single
rank, and moving it to M3 would let ``parsing`` and the source-code extractor past a gate
their implementations have not walked through yet -- a run that dies half-way is worse than
a clean refusal, and AI-CHAT's own delivery is exactly the case where a named list beats a
rank.  The API / UI / Fixture agents are delivered through :data:`DELIVERED_AGENTS` for the
same reason rather than by moving the rank, so that the extractor stays gated while the page
agents, whose prompt sources and evaluation sets now ship, do not.
"""

from __future__ import annotations

from factory_intelligence.enums import AgentType, Milestone, milestone_at_least
from factory_intelligence.errors import FeatureNotEnabled


def require_milestone(milestone: Milestone | str, what: str) -> None:
    """Raise unless ``milestone`` has been delivered in this build."""
    milestone = Milestone(milestone)
    if not milestone_at_least(milestone):
        raise FeatureNotEnabled(
            f"{what} is delivered in {milestone.value} and is not part of this build",
            details={"capability": what, "milestone": milestone.value},
        )


def require_capability(name: str, what: str) -> None:
    """Raise unless this named capability has been delivered (see :data:`DELIVERED_CAPABILITIES`)."""
    if name not in DELIVERED_CAPABILITIES:
        raise FeatureNotEnabled(
            f"{what} is not delivered in this build",
            details={"capability": name},
        )


def require_agent(agent_type: AgentType | str) -> None:
    """Raise unless this agent is delivered in the current milestone."""
    agent_type = AgentType(agent_type)
    if not agent_enabled(agent_type):
        raise FeatureNotEnabled(
            f"the {agent_type.value} agent is not delivered in this build",
            details={"agentType": agent_type.value},
        )


def agent_enabled(agent_type: AgentType | str) -> bool:
    """Whether ``agent_type`` may run here.

    The milestone decides (SRS 12), then :data:`DELIVERED_AGENTS`, then
    ``settings().enabled_agents`` lets a deployment opt an agent in early for its own
    testing without changing the library.
    """
    from factory_intelligence.config import settings

    agent_type = AgentType(agent_type)
    if agent_type.value in settings().enabled_agents:
        return True
    if agent_type in DELIVERED_AGENTS:
        return True
    return milestone_at_least(AGENT_MILESTONE[agent_type])


#: Which milestone delivers which agent (SRS 3.3 and 12).
AGENT_MILESTONE: dict[AgentType, Milestone] = {
    AgentType.REQUIREMENTS: Milestone.M2,
    AgentType.DOMAIN: Milestone.M2,
    AgentType.MAPPING: Milestone.M2,
    AgentType.METADATA_DESIGN: Milestone.M2,
    AgentType.API: Milestone.M3,
    AgentType.UI: Milestone.M3,
    AgentType.FIXTURE: Milestone.M3,
    AgentType.REVIEW: Milestone.M3,
}

#: Capabilities delivered ahead of the milestone SRS 12 schedules them for, because S1's
#: M3/M4 surface is built on them.  Each entry is a function that answers for real.
DELIVERED_CAPABILITIES: frozenset[str] = frozenset({"stage_chat", "clear_chat", "set_budget"})

#: Agents delivered ahead of schedule for the same reason.  AI-CHAT-001 makes the Review
#: Assistant the thing that answers in-stage chat, so chat cannot ship without it.
#:
#: API, UI and Fixture join it because PAGE_DESIGN is on the current design line: S1's
#: ``_AGENT_STAGES`` dispatches the stage and mode-B ``HANDOFF`` depends on the page intents
#: existing (``frontend page audit`` §8 converted the M3/M4 deferral clauses to current-period
#: delivery rather than restating them).  Nothing here is a placeholder for work that is
#: missing — the sixteen constraint rules those agents need, their :mod:`agents.spec` rows and
#: S2's section schemas for ``apiIntents`` / ``uiIntents`` / ``fixtures`` all shipped in M2, and
#: each of the three now ships a prompt source and an evaluation set beside it.  Their
#: ``milestone`` field still says M3, exactly as REVIEW's does: that is the schedule, and
#: ``enabled`` is what this build actually serves.
DELIVERED_AGENTS: frozenset[AgentType] = frozenset(
    {AgentType.REVIEW, AgentType.API, AgentType.UI, AgentType.FIXTURE}
)


__all__ = [
    "AGENT_MILESTONE",
    "DELIVERED_AGENTS",
    "DELIVERED_CAPABILITIES",
    "agent_enabled",
    "require_agent",
    "require_capability",
    "require_milestone",
]
