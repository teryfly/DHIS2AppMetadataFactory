"""Scheduled jobs (CP-SET-002).

S0 only provides the functions; S1 owns the schedule.  Under the Temporal engine
these become server-side schedules; under the in-process engine they run as an
asyncio loop in the worker so a development checkout behaves the same.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from factory_control.config import get_settings
from factory_control.foundation import get_logger, run_housekeeping
from factory_control.runtime import Runtime

_logger = get_logger("factory_control.scheduled")

HOURLY = 3600
DAILY = 86400

#: Each job's interval in seconds, as declared here; ``audit_link_interval_seconds`` overrides
#: the chaining job (CP-SET-002).  These were module constants frozen into :data:`JOBS` at import
#: time, which is why the configuration existed with nothing reading it.
DEFAULT_JOB_INTERVALS: dict[str, int] = {
    "auto_continue": 5,
    "link_audit": 5,
    "housekeeping": HOURLY,
    "verify_chain": DAILY,
    "purge_events": DAILY,
    "purge_llm_exchanges": DAILY,
    "retention": DAILY,
    "check_credentials": DAILY,
    "deliver_notifications": 300,
    "gate_reminders": 900,
}

#: Jobs whose interval a Settings field overrides, by ``job name -> settings attribute``.
JOB_INTERVAL_SETTINGS_KEYS: dict[str, str] = {"link_audit": "audit_link_interval_seconds"}

JOB_SCHEDULE_WORKFLOW = "CpScheduledJobs"


def job_interval(name: str, settings: Any = None) -> int:
    """The interval one scheduled job runs at, resolved per call (CP-SET-002).

    Resolution happens when the loop or the schedule asks for it rather than at import, so a
    deployment that sets ``FACTORY_AUDIT_LINK_INTERVAL_SECONDS`` gets what it asked for.  FD-AUD-004
    promises chaining within ten seconds of commit, so a value that is missing or not positive
    falls back to the declared default instead of parking the chain.
    """
    default = DEFAULT_JOB_INTERVALS[name]
    resolved = settings if settings is not None else get_settings()
    key = JOB_INTERVAL_SETTINGS_KEYS.get(name)
    if key is None:
        return default
    value = getattr(resolved, key, None)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return default
    return value


def job_link_audit(runtime: Runtime) -> dict[str, Any]:
    """Audit chaining, every 5 seconds (S0 FD-AUD-004: within 10 s of commit)."""
    from factory_control.services.audit_query import link_pending_now

    linked = link_pending_now(runtime)
    return {"job": "link_audit", "linked": linked}


def job_verify_chain(runtime: Runtime) -> dict[str, Any]:
    """Daily full-chain verification; a break raises a notification (CP-NTF-001)."""
    with runtime.transaction(operator="system") as ctx:
        from factory_control.foundation import run_full_chain_verify

        result = run_full_chain_verify(now=ctx.clock.now(), session=ctx.session)
    ok = bool(getattr(result, "ok", False))
    if not ok:
        from factory_control.services.notifications import notify_audit_integrity_failure

        notify_audit_integrity_failure(
            runtime,
            detail={
                "firstBrokenSequence": getattr(result, "first_broken_seq", None),
                "reason": getattr(result, "reason", None),
            },
        )
    return {"job": "verify_chain", "ok": ok}


def job_housekeeping(runtime: Runtime) -> dict[str, Any]:
    """S0 daily clean-up: expired staging, pending deletes, expired idempotency.

    Called with no unit of work open: S0's housekeeping touches the object store,
    which is external I/O and illegal inside a transaction (00 doc 6.2).
    """
    result = run_housekeeping(now=runtime.clock.now())
    return {"job": "housekeeping", "detail": result.as_dict()}


def job_purge_events(runtime: Runtime) -> dict[str, Any]:
    """Event-stream retention (CP-EVT-004)."""
    from factory_control.events.event_log import purge_expired
    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        removed = purge_expired(session, now=runtime.clock.now())
    return {"job": "purge_events", "removed": removed}


def job_purge_llm_exchanges(runtime: Runtime) -> dict[str, Any]:
    """S3 transcript retention: the log quotes the input, so it does not live forever."""
    removed = runtime.intelligence.purge_llm_exchanges()
    return {"job": "purge_llm_exchanges", "removed": removed}


def job_retention(runtime: Runtime) -> dict[str, Any]:
    """Archived-project artifact retention (CP-SET-001 / CP-PRJ-006)."""
    from datetime import timedelta

    from sqlalchemy import select

    from factory_control.foundation import apply_retention, unit_of_work
    from factory_control.models import Project

    cutoff_days = _archived_retention_days(runtime)
    with unit_of_work() as session:
        if cutoff_days <= 0:
            return {"job": "retention", "skipped": True, "reason": "RETENTION_DISABLED"}
        cutoff = runtime.clock.now() - timedelta(days=cutoff_days)
        projects = session.execute(
            select(Project).where(Project.archived.is_(True), Project.deleted_at.is_(None))
        ).scalars().all()
        purged = 0
        for project in projects:
            archived_at = project.archived_at
            if archived_at is None or archived_at > cutoff:
                continue
            purged += len(apply_retention(project.id, before=cutoff, session=session))
    return {"job": "retention", "projects": len(projects), "purgedArtifacts": purged}


def _archived_retention_days(runtime: Runtime) -> int:
    with runtime.transaction(operator="system") as ctx:
        return ctx.settings.archived_retention_days()


def job_check_credentials(runtime: Runtime) -> dict[str, Any]:
    """Daily credential-expiry sweep (CP-NTF-003)."""
    items = [
        {
            "environmentKey": c.environment_key,
            "secretRef": c.secret_ref,
            "expiresAt": c.expires_at.isoformat() if c.expires_at else None,
        }
        for c in runtime.execution.list_expiring_credentials(
            runtime.settings.credential_expiry_warning_days
        )
    ]
    if items:
        from factory_control.services.notifications import notify_credentials_expiring

        notify_credentials_expiring(runtime, items=items)
    return {"job": "check_credentials", "expiring": len(items)}


def job_deliver_notifications(runtime: Runtime) -> dict[str, Any]:
    """CP-NTF-002: retry queued channel deliveries."""
    from factory_control.services.notifications import deliver_pending

    return {"job": "deliver_notifications", **deliver_pending(runtime)}


def job_auto_continue(runtime: Runtime) -> dict[str, Any]:
    """Start stages the system settings mark as automatic (no Gate is bypassed)."""
    from factory_control.services.auto_continue import auto_continue_ready_stages

    return {"job": "auto_continue", **auto_continue_ready_stages(runtime)}


def job_gate_reminders(runtime: Runtime) -> dict[str, Any]:
    """CP-GATE-016: waiting Gates are reminded up to three times."""
    from factory_control.services.notifications import gate_reminder_due

    return {"job": "gate_reminders", "reminded": len(gate_reminder_due(runtime))}


JOBS: dict[str, Callable[[Runtime], dict[str, Any]]] = {
    "auto_continue": job_auto_continue,
    "link_audit": job_link_audit,
    "housekeeping": job_housekeeping,
    "verify_chain": job_verify_chain,
    "purge_events": job_purge_events,
    "purge_llm_exchanges": job_purge_llm_exchanges,
    "retention": job_retention,
    "check_credentials": job_check_credentials,
    "deliver_notifications": job_deliver_notifications,
    "gate_reminders": job_gate_reminders,
}


def run_all_once(runtime: Runtime, *, jobs: list[str] | None = None) -> dict[str, Any]:
    """Run the named jobs (or all of them) once.  Used by the CLI and tests."""
    results: dict[str, Any] = {}
    for name, fn in JOBS.items():
        if jobs and name not in jobs:
            continue
        try:
            results[name] = fn(runtime)
        except Exception as exc:  # noqa: BLE001 - a failing job must not stop the loop
            _logger.exception("scheduled job %s failed", name)
            results[name] = {"job": name, "error": type(exc).__name__, "detail": str(exc)[:300]}
    return results


async def run_inprocess_loop(runtime: Runtime, *, stop: asyncio.Event) -> None:
    """Development scheduler: tick every job at its own interval.

    The interval is read from this process's settings on every pass of the job list, so a value
    set in ``.deploy/env`` is what actually runs (CP-SET-002).
    """
    settings = runtime.settings
    tasks = [
        asyncio.create_task(_loop_for(name, fn, job_interval(name, settings), runtime, stop))
        for name, fn in JOBS.items()
    ]
    await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _loop_for(name: str, fn: Callable[[Runtime], dict[str, Any]], interval: int, runtime: Runtime, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.sleep(interval)
            if stop.is_set():
                return
            await asyncio.to_thread(fn, runtime)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            _logger.exception("scheduled loop %s failed", name)


async def ensure_temporal_schedules(client: Any, *, settings: Any) -> list[str]:
    """Create the server-side schedules for the Temporal engine (CP-SET-002).

    Returns the schedule ids that were created; an existing schedule is the normal
    case on restart, so it is skipped rather than treated as a failure.
    """
    from datetime import timedelta

    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleSpec,
    )

    created: list[str] = []
    for name in JOBS:
        interval = job_interval(name, settings)
        schedule_id = f"cp-{name}"
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                JOB_SCHEDULE_WORKFLOW,
                args=[{"job": name}],
                # {timestamp} keeps one workflow id per fire, as Temporal requires
                # for a schedule whose action is a workflow start.
                id=f"cp-{name}-{{timestamp}}",
                task_queue=settings.temporal_task_queue,
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=interval))]),
        )
        try:
            await client.create_schedule(id=schedule_id, schedule=schedule)
        except Exception as exc:  # noqa: BLE001 - already exists is the normal case
            _logger.debug("schedule %s not created: %s", schedule_id, exc)
        else:
            created.append(schedule_id)
    return created


__all__ = [
    "DAILY",
    "DEFAULT_JOB_INTERVALS",
    "HOURLY",
    "JOB_INTERVAL_SETTINGS_KEYS",
    "JOBS",
    "JOB_SCHEDULE_WORKFLOW",
    "ensure_temporal_schedules",
    "job_interval",
    "job_check_credentials",
    "job_deliver_notifications",
    "job_gate_reminders",
    "job_housekeeping",
    "job_link_audit",
    "job_purge_events",
    "job_purge_llm_exchanges",
    "job_retention",
    "job_verify_chain",
    "run_all_once",
    "run_inprocess_loop",
]
