"""CP-NFR-09 across the whole product: every code S1 can answer with is translated in *both* consoles.

:mod:`test_error_catalogue` keeps S1's own two languages level. This is the other half of the same
rule, one hop further out: the front ends hold their own error catalogues, and a code missing from
one of them is not caught by any front-end test — `translateErrorCode` falls back to the message the
raiser wrote, which is English prose aimed at a log. A Chinese operator therefore reads a sentence
nobody translated for them, on a screen whose job is to tell them what to do next.

Nothing checked the four catalogues against the server until now. Measured on the day this test was
written, 24 codes S1 raises had no entry in either console — among them every DHIS2 import failure
(`DX_*`, `DHIS2_TASK_FAILED`), the CSRF refusal, and `SECRET_IN_USE`, which is the answer the
credentials screen gives most often.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from factory_control.errors import ERROR_HTTP_STATUS, PROVIDER_ERROR_HTTP_STATUS, S1_ERROR_RETRYABLE

REPO = Path(__file__).resolve().parents[3]

#: The `errors: { … }` object of each catalogue, in source order.
_BLOCK = re.compile(r"\n  errors: \{(.*?)\n  \}", re.S)
_KEY = re.compile(r"([A-Z][A-Z0-9_]{2,})\s*:")

#: (path, which `errors` block in that file). The desktop keeps both languages in one module, so the
#: second block is its Chinese catalogue; the mobile console splits them by file.
CONSOLES = (
    ("S5/src/i18n/messages.ts", 0),
    ("S5/src/i18n/messages.ts", 1),
    ("S5M/src/i18n/locales/en.ts", 0),
    ("S5M/src/i18n/locales/zh-CN.ts", 0),
)


def _codes(relative: str, index: int) -> set[str]:
    source = (REPO / relative).read_text(encoding="utf-8")
    blocks = _BLOCK.findall(source)
    assert len(blocks) > index, f"{relative}: no `errors: {{ … }}` block #{index + 1} found — did the catalogue move?"
    return set(_KEY.findall(blocks[index]))


def _raisable() -> set[str]:
    return set(ERROR_HTTP_STATUS) | set(PROVIDER_ERROR_HTTP_STATUS) | set(S1_ERROR_RETRYABLE)


def test_the_server_can_raise_a_real_number_of_codes() -> None:
    # A vocabulary that collapsed to nothing would make every assertion below vacuously true.
    assert len(_raisable()) > 40


@pytest.mark.parametrize(("relative", "index"), CONSOLES)
def test_every_code_s1_can_answer_is_translated(relative: str, index: int) -> None:
    missing = sorted(_raisable() - _codes(relative, index))
    assert not missing, f"{relative} has no message for: {missing}"
