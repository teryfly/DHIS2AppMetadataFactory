"""Governance of a run against a shared live DHIS2 instance (FR-TST-002, FR-TST-003).

The official demo instances are public, shared, periodically reset by DHIS2, and writable by
anyone holding the published demo credentials.  That makes them the only test target where
"my test passed" and "I left the environment as I found it" are different claims, so the rules
in 25.4 are enforced here rather than trusted to each test:

① every object this run creates carries a code with the run tag in it, so a residue can be
   recognised as ours and swept by a later run;
② pre-existing demo objects are never modified -- only referenced -- and the ones a test does
   depend on are hash-checked before and after, which is the only way to *know* nothing was
   touched rather than to believe it wasn't;
③ teardown deletes what this run created, and a failed cleanup warns instead of failing,
   because the test's verdict is about the instance's behaviour, not about our own DELETE call;
④ synthetic data only (there is nothing here that could accept anything else);
⑤ one regression at a time per instance, across processes.

FR-TST-003 adds the third state a live run needs: an unreachable or resetting instance is
**not** a failure.  It is indeterminate, retried with a backoff, and reported as such -- and
`tests/contract` refuses to call it anything else.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: ① run tag.  Short enough to fit inside DHIS2's 255-character code, long enough that two
#: runs of the same commit do not collide.
RUN_TAG = uuid.uuid4().hex[:10]

#: the code prefix every written object carries.  ``FT_`` plus the tag plus the caller's own
#: name: a shared instance full of ``factory_contract_validate`` from fifty separate runs told
#: nobody which fifty runs wrote them, so residue could not be swept (FR-TST-002①).
CODE_PREFIX = f"FT_{RUN_TAG}_"

#: where the cross-process serial lock lives (FR-TST-002⑤)
_LOCK_DIR = Path(os.environ.get("FACTORY_DX_LIVE_LOCK_DIR", str(Path.home() / ".cache" / "damf")))

#: a lock older than this is treated as a crashed run, not a live one
_LOCK_STALE_SECONDS = 45 * 60


def code_for(name: str) -> str:
    """The code of one object this run creates.

    The old helper returned ``factory_contract_<suffix>`` unchanged when a caller passed a
    literal suffix, so every run wrote the *same* code: the second run on a shared instance
    updated the first run's object and read its own result back as a pass.
    """
    slug = "".join(char if char.isalnum() else "_" for char in name).strip("_").lower()
    return f"{CODE_PREFIX}{slug}"


def is_ours(code: str) -> bool:
    return str(code or "").startswith("FT_")


def is_this_run(code: str) -> bool:
    return str(code or "") == CODE_PREFIX or str(code or "").startswith(CODE_PREFIX)


# --- ③ the set of objects this run created, and its cleanup ----------------------


@dataclass
class Created:
    """What this run wrote, so teardown can remove exactly that and nothing more."""

    items: list[dict[str, str]] = field(default_factory=list)

    def remember(self, dhis2_type: str, uid: str | None = None, code: str | None = None) -> None:
        if not uid and not code:
            raise AssertionError("an object to clean up needs a uid or a code")
        self.items.append(
            {
                "dhis2Type": dhis2_type,
                "uid": uid or "",
                "code": code or "",
            }
        )

    def sweep(self, base_url: str, credentials: tuple[str, str]) -> dict[str, Any]:
        """Delete what this run created.  A failure warns; it never changes the verdict."""
        return _delete(base_url, credentials, self.items)

    def sweep_residue(self, base_url: str, credentials: tuple[str, str]) -> dict[str, Any]:
        """Delete objects earlier runs left behind (FR-TST-002③).

        A run killed between COMMIT and teardown cannot clean up after itself, and on a
        public shared instance the debris outlives the CI job.  Recognising it is what ①'s
        ``FT_<tag>_`` prefix is for.
        """
        found: list[dict[str, str]] = []
        for dhis2_type in _SWEEP_TYPES:
            for row in _list_by_code_prefix(base_url, credentials, dhis2_type, "FT_"):
                found.append({"dhis2Type": dhis2_type, "uid": str(row.get("id") or "")})
        return _delete(base_url, credentials, found)


#: the types a contract run writes; the residue sweep looks for these and nothing else, so a
#: mistaken sweep cannot go looking through an instance's whole metadata catalogue
_SWEEP_TYPES = ("dataElement", "optionSet", "option", "categoryOption", "program")


def _request(
    base_url: str,
    credentials: tuple[str, str],
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    """One direct DHIS2 call, as a test utility.

    Deliberately not routed through S4: the profile catalogue carries the logical resources
    the *product* uses, and ``DELETE /api/{type}/{uid}`` plus a by-code lookup are not among
    them.  Adding them to a shipped Profile to let a test tidy up after itself would put a
    cleanup convenience into the contract every deployment is compiled against.  httpx with
    the same credentials the product resolves is the honest shape for "the test removes what
    the test wrote".
    """
    import httpx

    username, password = credentials
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}/api/{path}",
        params=params or {},
        auth=(username, password),
        timeout=60.0,
        headers={"Accept": "application/json"},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:200]}")
    if not response.content:
        return {}
    body: Any = response.json()
    return body


def _list_by_code_prefix(
    base_url: str, credentials: tuple[str, str], dhis2_type: str, prefix: str
) -> list[dict[str, Any]]:
    body = _request(
        base_url,
        credentials,
        "GET",
        _collection(base_url, dhis2_type),
        params={
            "fields": "id,code,name",
            "filter": f"code:like:{prefix}",
            "paging": "false",
        },
    )
    rows = body.get(dhis2_type) if isinstance(body, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _delete(
    base_url: str, credentials: tuple[str, str], items: list[dict[str, str]]
) -> dict[str, Any]:
    removed = 0
    warnings: list[str] = []
    for row in reversed(items):
        dhis2_type, uid = row.get("dhis2Type") or "", row.get("uid") or ""
        if not uid:
            # a COMMIT that was never followed by a uid read back has nothing addressable;
            # deleting by name guess on a shared instance is how other people's data dies
            warnings.append(f"{dhis2_type}: no uid recorded, not deleted")
            continue
        try:
            _request(base_url, credentials, "DELETE", f"{_collection(base_url, dhis2_type)}/{uid}")
            removed += 1
        except Exception as exc:  # noqa: BLE001 -- ③: a cleanup failure is a warning
            warnings.append(f"{dhis2_type}/{uid}: {exc}")
    return {"removed": removed, "warnings": warnings}


# --- ② the demo objects a run leans on must come back unchanged -------------------


def content_fingerprint(document: dict[str, Any]) -> str:
    """A hash of an object's content with the fields the instance itself rewrites removed.

    Same exclusions as S4's discovery normalisation (``lastUpdated``, ``href``, ``access``,
    ``comments``): DHIS2 refreshes those on any touch, so a fingerprint that keeps them would
    report "someone changed the demo data" for a read.
    """
    volatile = {"lastUpdated", "lastVersionTime", "href", "access", "comments", "internalName"}
    pruned = {key: value for key, value in sorted(document.items()) if key not in volatile}
    return hashlib.sha256(json.dumps(pruned, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass
class DemoBaseline:
    """Hashes of the pre-existing objects this run references (DX-AC-15)."""

    before: dict[str, str] = field(default_factory=dict)

    def remember(self, label: str, document: dict[str, Any]) -> None:
        self.before[label] = content_fingerprint(document)

    def differences(
        self, base_url: str, credentials: tuple[str, str]
    ) -> list[str]:
        """Labels whose current content no longer matches what was seen at the start."""
        changed: list[str] = []
        for label, seen in sorted(self.before.items()):
            current = _read_by_label(base_url, credentials, label)
            if current is None:
                changed.append(f"{label}: vanished from the instance")
            elif content_fingerprint(current) != seen:
                changed.append(f"{label}: content changed")
        return changed


class CollectionUnresolvedError(RuntimeError):
    """The shipped catalogues cannot name the API collection of a type on this instance.

    Its only purpose is to be **not** an ordinary read failure: :meth:`DemoBaseline.differences`
    swallows a failed read and calls it "the instance lost it", and a lookup that never left
    this process must not be reported as a change in a shared public environment.
    """


def _collection(base_url: str, dhis2_type: str) -> str:
    """The API collection of a metadata type, from the shipped version Profile.

    The plural is not a rule one can guess: ``dataElement`` becomes ``dataElements`` but
    ``categoryOption`` becomes ``categoryOptions`` while ``attribute`` becomes
    ``attributes``, and the Profile is where the instance's own answer is recorded.  Reading
    a demo object through a wrong collection answers 404, and a 404 in
    :meth:`DemoBaseline.differences` reads as "the instance lost it" -- a false alarm about a
    shared public environment, which is the one thing DX-AC-15 must never produce.
    """
    from factory_dhis2.profiles import registry
    from factory_dhis2.profiles.base import TYPE_RESOURCE_PREFIX

    registry.load_builtin_profiles()
    profile = registry.profile_for_version(_catalogued_version(base_url))
    entry = profile.resource(f"{TYPE_RESOURCE_PREFIX}{dhis2_type}")
    if entry is None:
        raise CollectionUnresolvedError(
            f"profile for {_catalogued_version(base_url)!r} has no type {dhis2_type!r}"
        )
    return str(entry.path)


def _catalogued_version(base_url: str) -> str:
    """The version the catalogue names for this instance (FR-TST-001).

    Not a separate environment variable: which instance to talk to and which Profile its
    endpoints come from must be one decision, or updating the catalogue leaves the cleanup
    reading a different version's endpoint table.
    """
    wanted = base_url.rstrip("/")
    for item in catalogue()["instances"]:
        if str(item["baseUrl"]).rstrip("/") == wanted:
            return str(item["version"])
    raise CollectionUnresolvedError(
        f"{wanted} is not in tests/contract/live_instances.json; "
        f"catalogued: {', '.join(supported_versions())}"
    )


def _read_by_label(
    base_url: str, credentials: tuple[str, str], label: str
) -> dict[str, Any] | None:
    """Fetch one object by ``dhis2Type:uid``; the label is that pair, joined by a colon."""
    dhis2_type, _, uid = label.partition(":")
    if not uid:
        return None
    # resolved outside the try on purpose: a catalogue we cannot read is not the instance
    # losing an object, and only the second of those is a finding.
    collection = _collection(base_url, dhis2_type)
    try:
        body = _request(
            base_url, credentials, "GET", f"{collection}/{uid}", params={"fields": ":all"}
        )
    except Exception:  # noqa: BLE001 -- an object we cannot read is reported by differences()
        return None
    return body if isinstance(body, dict) else None


# --- ⑤ one run at a time per instance --------------------------------------------


class InstanceLock:
    """A cross-process advisory lock so a shared instance sees one regression at a time.

    Written as a directory rather than a file because ``mkdir`` is atomic on every filesystem
    that matters here, and the check-and-create has no window.  A lock is not released on a
    timeout -- a run that is merely slow must not be stepped on -- but a lock older than
    ``_LOCK_STALE_SECONDS`` belongs to a process that died, and taking it over is the only way
    the daily schedule can recover by itself.
    """

    def __init__(self, base_url: str, *, wait_seconds: float = 0.0, sleep: Any = time.sleep) -> None:
        self.key = hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16]
        self.path = _LOCK_DIR / f"dx-live-{self.key}.lock"
        self.wait_seconds = wait_seconds
        self._sleep = sleep
        self._held = False

    def acquire(self) -> InstanceLock:
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                self.path.mkdir()
                self._held = True
                (self.path / "owner").write_text(
                    json.dumps({"pid": os.getpid(), "tag": RUN_TAG, "takenAt": time.time()})
                )
                return self
            except FileExistsError:
                if self._steal_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"another regression run holds {self.path}; FR-TST-002⑤ serialises them"
                    ) from None
                self._sleep(5.0)

    def _steal_if_stale(self) -> bool:
        stamp = self.path / "owner"
        try:
            taken = float(json.loads(stamp.read_text()).get("takenAt", 0.0))
        except (OSError, ValueError):
            taken = 0.0
        if time.time() - taken < _LOCK_STALE_SECONDS:
            return False
        self.release()
        return True

    def release(self) -> None:
        import shutil

        shutil.rmtree(self.path, ignore_errors=True)
        self._held = False

    def __enter__(self) -> InstanceLock:
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


# --- FR-TST-003: indeterminate is a third answer, not a failure -------------------


_INDETERMINATE_MARKERS = (
    "unreachable",
    "connection",
    "timed out",
    "timeout",
    "reset",
    "temporarily unavailable",
    "bad gateway",
    "gateway timeout",
    "429",
    "502",
    "503",
    "504",
    "ssl",
    "dns",
    "name resolution",
    "name or service not known",
    "no route to host",
    "connection aborted",
)


def is_indeterminate(error: BaseException | str) -> bool:
    """Is this an instance problem rather than a Factory problem?

    DHIS2's demo instances go down, are reset by the vendor mid-run, and answer 5xx under load.
    Scoring that as a Factory failure makes the daily regression meaningless -- it reports the
    vendor's day, not our build -- and gets the run ignored, which is how a real breakage
    eventually hides in the noise (FR-TST-003).
    """
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
        return True
    text = str(error).lower()
    return any(marker in text for marker in _INDETERMINATE_MARKERS)


@dataclass
class Attempt:
    """One indeterminate-tolerant attempt, with the backoff FR-TST-003 asks for."""

    outcome: str  # "passed" | "failed" | "indeterminate"
    result: Any = None
    error: BaseException | None = None
    attempts: int = 1


def run_with_retries(
    work: Any,
    *,
    retries: int = 3,
    backoff_seconds: list[float] | None = None,
    sleep: Any = time.sleep,
) -> Attempt:
    """Call ``work`` until it gives a verdict, retrying only indeterminate outcomes.

    A genuine assertion failure is returned immediately: retrying it would turn a real
    difference into a pass once the instance happens to answer the way we expected.
    """
    waits = backoff_seconds or [30.0, 120.0, 480.0]
    last: Attempt | None = None
    for index in range(retries + 1):
        try:
            return Attempt("passed", result=work(), attempts=index + 1)
        except AssertionError as exc:
            return Attempt("failed", error=exc, attempts=index + 1)
        except Exception as exc:  # noqa: BLE001 -- classified below, not swallowed
            if not is_indeterminate(exc):
                raise
            last = Attempt("indeterminate", error=exc, attempts=index + 1)
            if index < retries:
                sleep(waits[min(index, len(waits) - 1)])
    assert last is not None
    return last


# --- FR-TST-001: the instance catalogue is configuration, not code ---------------


def catalogue() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "live_instances.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def instance_for_version(version: str) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = [dict(row) for row in catalogue()["instances"]]
    for row in rows:
        listed = str(row["version"])
        if listed == version or listed.startswith(f"{version}."):
            return row
    return None


def supported_versions() -> list[str]:
    return [str(item["version"]) for item in catalogue()["instances"]]


def newest_patch_known(version: str) -> str | None:
    """The patch version the catalogue pins, for the daily reminder (DX-TST-005).

    When the instance answers with a newer patch than the catalogue lists, the schedule says
    so: FR-TST-001 asks for the configuration to be updated and the regression re-run, which
    nobody does unless something notices the drift.
    """
    wanted = version.split("-")[0] if "-" in version else version
    for item in catalogue()["instances"]:
        if str(item["version"]).rsplit(".", 1)[0] == wanted.rsplit(".", 1)[0]:
            return str(item["version"])
    return None


def live_base_url() -> str:
    """The instance to talk to.

    Deliberately with **no** built-in default: FR-TST-001 puts the list in configuration, and
    a code-level fallback turns a missing or misspelled environment variable into a run against
    whatever instance the constant happens to name -- reported as a passing regression.
    """
    explicit = os.environ.get("FACTORY_DX_LIVE_DHIS2_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    named = os.environ.get("FACTORY_DX_LIVE_DHIS2_VERSION")
    if named:
        item = instance_for_version(named)
        if item:
            return str(item["baseUrl"])
    raise RuntimeError(
        "no live instance configured: set FACTORY_DX_LIVE_DHIS2_BASE_URL (or "
        "FACTORY_DX_LIVE_DHIS2_VERSION, one of " + ", ".join(supported_versions()) + ")"
    )
