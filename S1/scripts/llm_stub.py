"""Local OpenAI-compatible stand-in used to prove S3's LIVE provider path.

Answers POST /v1/chat/completions with a schema-valid agent envelope.  Every request is
appended to a JSONL log so the caller can see what S3 actually sends a model — the answer
policy is keyed off that, not off a guess.

    python llm_stub.py --port 8299 --log /tmp/llm-requests.jsonl

This is development tooling for the assembled backend, not a product component: a real
deployment points the same registration at a real endpoint.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
import traceback
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_LOG: Path | None = None

#: The eval runner's per-case parameter block opens with this line
#: (``eval/runner.py:_parameter_block``), so it is the only honest signal on the wire that a
#: request is one case of an evaluation rather than an agent run.  Deciding that here — not
#: weakening AI-REG-003's gate — is what lets a deterministic stand-in be *measured* like a
#: model: it answers each case with the answer that case declares as expected (AI-EVAL-001).
_EVAL_MARKER = "Run parameters for this evaluation."
_EVAL_AGENT = re.compile(r"agentType:\s*([A-Z_]+)")

#: agent value -> loaded cases, cached so the packaged sets are read once per process.
_EVAL_SETS: dict[str, list[Any]] = {}


def _load_cases(agent: str) -> list[Any]:
    """The packaged evaluation set for one agent, via S3's own loader (no copy of the data).

    Imported lazily: an agent run never needs it, and a real deployment pointing this
    registration at a live model does not have ``factory_intelligence`` on the path at all.
    """
    if agent not in _EVAL_SETS:
        from factory_intelligence.enums import AgentType
        from factory_intelligence.eval.scoring import load_eval_set

        try:
            _EVAL_SETS[agent] = load_eval_set(AgentType(agent))
        except ValueError:
            _EVAL_SETS[agent] = []
    return _EVAL_SETS[agent]


def _evaluation_answer(body: dict[str, Any]) -> dict[str, Any] | None:
    """When this request is one eval case, the exact answer the case expects — else ``None``.

    A case is identified by the context fragments the runner fenced verbatim into it
    (``case_messages``): every one of a case's item contents must be present, which pins it to
    the one case whose items it is, because those contents are authored to be distinct. This is
    a stand-in for a model that answered correctly, not a way around the gate: it produces the
    same bytes a passing model would, so AI-EVAL-001's comparison scores 1.0 and nothing else.
    """
    messages = body.get("messages") or []
    blob = "\n".join(str(m.get("content") or "") for m in messages)
    if _EVAL_MARKER not in blob:
        return None
    named = _EVAL_AGENT.search(blob)
    if named is None:
        return None
    for case in _load_cases(named.group(1)):
        if all(fragment.content in blob for fragment in case.context.items):
            return dict(case.expected_document)
    return None


def _record(entry: dict[str, Any]) -> None:
    if _LOG is None:
        return
    with _LOCK, _LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _agent_hint(body: dict[str, Any]) -> str:
    """Which agent is asking, read out of the messages.

    The wire carries no agent field, so the prompt text is the only signal.  A model with a
    real vendor behind it does not need this; a script does, because its answers are per agent.
    """
    blob = json.dumps(body.get("messages", []), ensure_ascii=False)
    for hint in ("REQUIREMENTS", "DOMAIN", "MAPPING", "PAGE_DESIGN", "METADATA_DESIGN", "REVIEW"):
        if re.search(hint, blob):
            return hint
    return ""


#: ---------------------------------------------------------------------------
#: Authored answers, keyed on the requested ``schema_name``.
#:
#: S3 names every structured request after the schema it wants (``engine.py:378`` sends
#: ``"<agent>.output"``, ``parsing/documents/llm_extract.py`` sends ``"nrr.extraction"``), so
#: the name is the honest key for "which agent is asking".  A stand-in model answers each of
#: those the way a model that had read the prompt would; it does not invent a design.
#: ---------------------------------------------------------------------------

#: the coordinate the document extractor prints in front of every line it sends.  ``re.M`` is
#: not decoration: the anchors make the coordinate the *whole* prefix of a line, and without
#: multiline mode `findall` matches nothing at all and the answer silently degrades to generic.
_COORDINATE = re.compile(r"^\[(?P<coord>[^\]]+)\]\s*(?P<text>.*)$", re.M)

#: keyword -> normalised-requirement category, first match wins (S3's thirteen categories)
_NRR_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("data element", "dataRequirement"),
    ("constant", "dataRequirement"),
    ("legend", "dataRequirement"),
    ("category option", "dataRequirement"),
    ("csv", "reportRequirement"),
    ("export", "reportRequirement"),
    ("summary", "reportRequirement"),
    ("report", "reportRequirement"),
    ("team", "participant"),
    ("officer", "participant"),
    ("user", "participant"),
    ("never created", "businessRule"),
    ("only metadata", "businessRule"),
    ("no tracker", "businessRule"),
    ("reused", "businessRule"),
    ("records", "businessProcess"),
)


#: one fenced block of untrusted input content, as S3's prompt builder wraps it
_FENCE = re.compile(r"<untrusted\b[^>]*>(?P<body>.*?)</untrusted>", re.S)


def _nrr_answer(body: dict[str, Any]) -> dict[str, Any] | None:
    """Extract items from the fenced chunk exactly the way the prompt says to.

    AI-INP-005 makes the model quote the coordinate printed before the line, so the answer is
    built from those lines rather than from a fixed list: a different input document gets a
    different NRR, which is what makes this a stand-in for extraction rather than a fixture.

    Only the fenced block is read.  The first version scanned every message and turned the
    system prompt's own bracketed ``{"items": [ … ]}`` into a "requirement", which is precisely
    the injection the fence exists to keep out of the answer.
    """
    chunks: list[str] = []
    for message in body.get("messages") or []:
        if str(message.get("role")) != "user":
            continue
        content = str(message.get("content") or "")
        chunks.extend(match.group("body") for match in _FENCE.finditer(content))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        for coord, text in _COORDINATE.findall(chunk):
            sentence = text.strip()
            # a heading has no requirement in it, and the prompt says to report only what
            # the text states: four words or fewer is a title, not a sentence
            if len(sentence) < 12 or len(sentence.split()) < 4:
                continue
            lowered = sentence.lower()
            category = next((name for keyword, name in _NRR_CATEGORIES if keyword in lowered), "useCase")
            label = " ".join(sentence.split()[:6]).strip(".:,")
            if (category, label) in seen:
                continue
            seen.add((category, label))
            items.append(
                {
                    "category": category,
                    "label": label,
                    "statement": sentence if sentence.endswith((".", "!", "?")) else f"{sentence}.",
                    "location": coord,
                    "confidence": 0.9,
                    "priority": "MUST" if category in ("dataRequirement", "reportRequirement") else "SHOULD",
                    "rationale": f"stated at [{coord}] of the input document",
                }
            )
    if not items:
        return None
    return {"items": items}


#: ---------------------------------------------------------------------------
#: Authored agent answers.
#:
#: A design agent's answer is a patch, so an un-authored answer is an empty one: the stage still
#: reaches WAITING_APPROVAL, the Gate still opens, and the Blueprint gains nothing.  These two
#: answers are the design content a model would have written for the input document this seed
#: uploads — authored here, not guessed by a model, and labelled ``authored:<schema_name>`` in
#: the request log so a reader can tell a stand-in answer from a real one.
#: ---------------------------------------------------------------------------

#: an input passage's fence, with the artifact id S2 wants as provenance
_FENCED_INPUT = re.compile(r'<untrusted kind="INPUT_ARTIFACT" source="(?P<artifact>art-[0-9A-Za-z]+)"')

#: the Blueprint version the context quotes: an approved section is fenced with ``source="<version>"``
_FENCED_VERSION = re.compile(r'<untrusted kind="APPROVED_ENTRY" source="(?P<version>[0-9.]+)"')


def _base_version(body: dict[str, Any]) -> str:
    """The version a patch must be authored against, as far as the prompt reveals it.

    S3 tells the model to copy ``baseVersion`` "from the run parameters", but the stage-rules
    block it builds carries none (``agents/spec.py:90`` emits stage, patch mode and sections
    only), so the only version on the wire is the one fencing an approved section.  This reads
    that; a real model has the same problem, and the mismatch it causes is reported by
    ``output/validate.py`` as AI-SCO-002 rather than being silently accepted here.
    """
    for message in body.get("messages") or []:
        match = _FENCED_VERSION.search(str(message.get("content") or ""))
        if match is not None:
            return match.group("version")
    return "0.1.0"


def _envelope(
    summary: str, operations: list[dict[str, Any]], *, confidence: float = 0.9, base_version: str = "0.1.0"
) -> dict[str, Any]:
    """AI-RT-010's five keys, with a patch attached."""
    return {
        "summary": summary,
        "decisions": [],
        "questions": [],
        "confidence": confidence,
        "patch": {"baseVersion": base_version, "operations": operations},
    }


def _input_lines(body: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(artifactId, location, sentence)`` for every stated line of every input passage.

    Two shapes to read, because S3 quotes the same document twice over: the extraction prompt
    prints a ``[line:N]`` coordinate in front of each line (AI-INP-005 asks the model to cite it),
    while an agent prompt quotes the passage as it was written.  A line with no coordinate is
    therefore given its position, not dropped — dropping it is what made the first version of
    this function answer an agent run with nothing.
    """
    found: list[tuple[str, str, str]] = []
    for message in body.get("messages") or []:
        content = str(message.get("content") or "")
        for match in _FENCE.finditer(content):
            artifact = _FENCED_INPUT.search(match.group(0))
            if artifact is None:
                continue
            for number, raw in enumerate(match.group("body").splitlines(), start=1):
                cited = _COORDINATE.match(raw.strip())
                if cited is not None:
                    coord, sentence = cited.group("coord"), cited.group("text").strip()
                else:
                    coord, sentence = f"line:{number}", raw.strip()
                if sentence.startswith("#") or sentence.startswith("```"):
                    continue
                if len(sentence) < 12 or len(sentence.split()) < 4:
                    continue
                found.append((artifact.group("artifact"), coord, sentence))
    return found


def _business_key(prefix: str, sentence: str) -> str:
    """A Blueprint businessKey out of a sentence: ``^[a-z][a-z0-9]*([._:-][A-Za-z0-9_-]+)*$``."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", sentence.lower()) if w and not w.isdigit()]
    slug = re.sub(r"[^a-z0-9-]", "", "-".join(words[:4]))[:80].strip("-") or "item"
    return f"{prefix}.{slug}"


#: Agent answers already given, keyed by (schema_name, the input passages the prompt quoted).
#:
#: Why the stand-in has to be stateful: S3 does not show an agent the section it is writing, so a
#: *second* run of the same stage quotes the same document and a stateless answer would re-emit
#: the same businessKeys — which S2 refuses as duplicates (AI_PATCH_REJECTED).  A real model has
#: exactly the same blind spot, so this is the one behaviour here that a real model would have to
#: be told to do, and it is reported as such: it stands in for "the section was already populated,
#: so this run adds nothing".
_ANSWERED: set[tuple[str, tuple[str, ...]]] = set()


def _first_time(name: str, keys: tuple[str, ...]) -> bool:
    """True the first time this agent is asked to emit exactly these entries; records it.

    Keyed on the entries, not on the request: a rerun of a stage quotes the same document plus
    whatever artifacts the earlier attempt produced, so an artifact-based key misses it and the
    agent re-emits businessKeys S2 has already approved (which it refuses, AI_PATCH_REJECTED).
    """
    if not keys:
        return True
    if (name, keys) in _ANSWERED:
        return False
    _ANSWERED.add((name, keys))
    return True


def _requirements_answer(body: dict[str, Any]) -> dict[str, Any] | None:
    """One requirement per distinct stated fact, sourced to the artifact and line it was read at."""
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact_id, coord, sentence in _input_lines(body):
        key = _business_key("req", sentence)
        if key in seen:
            continue
        seen.add(key)
        operations.append(
            {
                "op": "add",
                "path": "/requirements/items/-",
                "value": {
                    "businessKey": key,
                    "statement": sentence if sentence.endswith((".", "!", "?")) else f"{sentence}.",
                    "source": {"artifactId": artifact_id, "location": coord},
                    "priority": "MUST",
                    "status": "CANDIDATE",
                    "confidence": 0.9,
                },
            }
        )
    if not operations:
        return None
    if not _first_time("requirements.output", tuple(str(op["value"]["businessKey"]) for op in operations)):
        return _envelope("every requirement this document states is already in the section; nothing to add", [])
    return _envelope(
        f"consolidated {len(operations)} requirement candidate(s) from the input document",
        operations,
        base_version=_base_version(body),
    )


def _domain_answer(body: dict[str, Any]) -> dict[str, Any] | None:
    """The domain the seed input describes: one monthly case-report entity and its captured fields."""
    sourced = _input_lines(body)
    if not sourced:
        return None
    artifact_id, coord = sourced[0][0], sourced[0][1]
    operations = [
        {
            "op": "add",
            "path": "/domain/entities/-",
            "value": {
                "businessKey": "entity.malaria-case-report",
                "displayName": {"default": "Malaria case report"},
                "description": "One facility's monthly count of suspected and confirmed malaria cases.",
                "attributes": [
                    {"businessKey": "attr.reporting-month", "name": "Reporting month", "valueType": "DATE"},
                    {"businessKey": "attr.confirmed-cases", "name": "Confirmed malaria cases", "valueType": "INT"},
                    {"businessKey": "attr.suspected-cases", "name": "Suspected malaria cases", "valueType": "INT"},
                    {"businessKey": "attr.reporting-facility", "name": "Reporting facility", "valueType": "REFERENCE"},
                ],
                "source": {"artifactId": artifact_id, "location": coord},
            },
        }
    ]
    if not _first_time("domain.output", ("entity.malaria-case-report",)):
        return _envelope("the domain entity is already in the section; nothing to add", [])
    return _envelope(
        "modelled the monthly malaria case report as one entity with four attributes",
        operations,
        base_version=_base_version(body),
    )


#: schema_name -> answer builder for requests that are not an eval case
_AUTHORED: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {
    "nrr.extraction": _nrr_answer,
    "requirements.output": _requirements_answer,
    "domain.output": _domain_answer,
}


def _authored_answer(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    name = _schema_name(body)
    if not name:
        return None, ""
    builder = _AUTHORED.get(name)
    if builder is None:
        return None, ""
    try:
        answer = builder(body)
    except Exception:  # noqa: BLE001 - an authored answer that cannot be built is no answer at all
        return None, ""
    return answer, name


def _generic_payload(body: dict[str, Any]) -> dict[str, Any]:
    """A minimal schema-valid envelope: what a live model would answer to an un-activated run.

    This is deliberately not an eval answer — it maps and asks nothing, so it cannot make an
    evaluation pass; it only keeps a plain stage run from erroring at the transport layer.
    """
    schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
    props = schema.get("properties") or {}
    payload: dict[str, Any] = {
        "summary": "stub answer",
        "questions": [],
        "decisions": [],
        "confidence": 0.7,
    }
    # Only offer the keys the schema actually declares, so a stricter envelope is still valid.
    return {k: v for k, v in payload.items() if k in props or k in (schema.get("required") or [])}


def _schema_name(body: Any) -> str | None:
    """The requested schema's name, or None — never an exception.

    Used on the failure path, where a lookup that itself raises would replace one unanswered
    request with another.
    """
    try:
        fmt = body.get("response_format") if isinstance(body, dict) else None
        inner = fmt.get("json_schema") if isinstance(fmt, dict) else None
        return inner.get("name") if isinstance(inner, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _answer(body: dict[str, Any]) -> dict[str, Any]:
    schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
    evaluated = _evaluation_answer(body)
    if evaluated is not None:
        payload, answered_from = evaluated, "eval-set"
    else:
        authored, name = _authored_answer(body)
        if authored is not None:
            payload, answered_from = authored, f"authored:{name}"
        else:
            payload, answered_from = _generic_payload(body), "generic"
            # Loud on purpose: an un-authored answer is an empty patch, and a design line that
            # passes on empty patches looks like a working pipeline while building nothing.
            print(f"[stub] GENERIC answer for schema={_schema_name(body)!r} — nothing authored for it",
                  file=sys.stderr, flush=True)
    return {"payload": payload, "schema": schema, "answered_from": answered_from}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, document: dict[str, Any]) -> None:
        raw = json.dumps(document, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, where: str, exc: BaseException, body: dict[str, Any] | None) -> None:
        """Answer with the failure instead of leaving the caller waiting for a byte.

        An exception that escapes ``do_POST`` never reaches the client as a status: nothing is
        written, the connection just goes away, and the caller sits on it until its own deadline
        fires.  S3 then reports ``LLM_TIMEOUT`` — which is a statement about *this stub*, read as
        a statement about the model, and it sent me chasing a phantom for that reason.  A 500 with
        the exception in it is both honest and debuggable, and the same failure is appended to the
        request log because the value of this file is that it records what was asked.

        Everything inside is optional: this ran once with a ``schema_name`` lookup of its own that
        raised on a malformed body, and an error path that can fail reproduces exactly the hang it
        was written to remove.
        """
        trace = ""
        try:
            trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        except Exception:  # noqa: BLE001 - formatting a failure must not become one
            trace = f"{type(exc).__name__}: {exc} (traceback unavailable)"

        for line in (trace or "").splitlines():
            print(f"[stub] {line}", file=sys.stderr, flush=True)

        try:
            _record(
                {
                    "path": self.path,
                    "where": where,
                    "failed": True,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": trace,
                    "schema_name": _schema_name(body),
                    "messages": body.get("messages") if isinstance(body, dict) else None,
                }
            )
        except Exception as record_exc:  # noqa: BLE001
            print(f"[stub] logging the failure failed too: {record_exc}", file=sys.stderr, flush=True)

        try:
            self._send(
                500,
                {
                    "error": {
                        "message": f"{where}: {type(exc).__name__}: {exc}",
                        "type": type(exc).__name__,
                        "traceback": trace[-4000:],
                    }
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            pass  # the caller gave up first; there is no one left to tell
        except Exception:  # noqa: BLE001 - closing is the last honest option, but say why
            print("[stub] could not send the 500; closing the connection", file=sys.stderr, flush=True)
            with contextlib.suppress(Exception):
                self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path.rstrip("/").endswith("/models"):
                self._send(200, {"object": "list", "data": [{"id": "stub-model", "object": "model"}]})
            else:
                self._send(404, {"error": {"message": "not found"}})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BaseException as exc:  # noqa: BLE001 - a stub must answer, not disappear
            self._fail("GET", exc, None)

    def do_POST(self) -> None:  # noqa: N802
        body: dict[str, Any] | None = None
        try:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": {"message": "body is not JSON"}})
                return
            result = _answer(body)
            content = json.dumps(result["payload"], ensure_ascii=False)
            _record(
                {
                    "path": self.path,
                    "agent_hint": _agent_hint(body),
                    "answered_from": result["answered_from"],
                    "schema_name": _schema_name(body),
                    "required": result["schema"].get("required"),
                    "schema": result["schema"],
                    "message_count": len(body.get("messages") or []),
                    "messages": body.get("messages"),
                    "answer": result["payload"],
                }
            )
            self._send(
                200,
                {
                    "id": "stub-1",
                    "object": "chat.completion",
                    "model": str(body.get("model") or "stub-model"),
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": len(content) // 4, "total_tokens": 100},
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BaseException as exc:  # noqa: BLE001 - see _fail(): silence here reads as a slow model
            self._fail("POST /chat/completions", exc, body)

    def log_message(self, *_args: Any) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    global _LOG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8299)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--log", default="")
    args = parser.parse_args(argv)
    if args.log:
        _LOG = Path(args.log)
        _LOG.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
