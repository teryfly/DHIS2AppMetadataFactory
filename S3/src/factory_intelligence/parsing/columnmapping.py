"""AI-INP-006: the column mapping, in one place.

Excel/CSV is the "结构化清单" row of AI-INP-002 — a deterministic parser *plus a column
mapping*, with the *mapping* the thing a human reviews.  Both tabular parsers used to carry
their own reading of a header row, which meant a header could be classified one way in a
CSV and another way in the same table saved as a workbook.  This module is now the only
place that answers "what does this column mean?"; :mod:`~factory_intelligence.parsing.tabular.csvparser`
and :mod:`~factory_intelligence.parsing.tabular.xlsxparser` differ only in *where* a cell
is, and pass that difference in as ``location_of``.

Three rules, each of which is a reading of AI-INP-006 a reviewer should be able to contest
in one edit:

* **Matching is data.**  :data:`HEADER_CATEGORY_RULES` is a table of normalised-header
  patterns onto the thirteen categories of AI-INP-005, tried in order, first match wins.
  A pattern matches the *normalised* header (NFKC, lowercased, non-alphanumeric runs
  collapsed to ``_``), which is why ``"字段"``, ``" Field Name "`` and ``"field-name"``
  all reach the same row.
* **No mapping supplied still parses — but confirms nothing.**  The proposal is recorded,
  the fragment is marked ``needs_review``, exactly one question is raised per sheet, and
  every entry of that fragment is ``CANDIDATE``.  With a mapping supplied it is used as-is
  and no question is asked, so the answer to an AI-INP-006 question re-parses to
  ``CONFIRMED`` entries rather than asking again.
* **An unrecognisable table is asked about, never guessed at.**  :func:`analyse_table`
  returns ``None`` and the caller asks; picking a category at random would put a wrong
  fact in the NRR that a human then has to notice.

The question this module raises carries two suggestions — the proposed mapping itself, and
"keep the file's own order" — because an answer that only says "yes" cannot be applied:
S1 records which suggestion was chosen, and re-parsing needs the mapping it names.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_intelligence.enums import NrrStatus, QuestionSeverity
from factory_intelligence.parsing.base import (
    Fragment,
    Location,
    NrrCategory,
    NrrEntry,
    business_key,
    disambiguate,
)
from factory_intelligence.parsing.confidence import AFFECTS_PREFIX

#: How many ``affects`` paths one question may name.  The agent envelope caps the list at
#: 40 (``output.schema``), and a question naming three hundred columns is not answerable.
AFFECTS_LIMIT = 40

#: The whole header → category mapping, matched in order against the *normalised* header
#: name; first match wins and the table's own category is the highest-priority match.
#:
#: Each row is a judgement about how a real requirements workbook is written and names the
#: category of AI-INP-005 it feeds.  These are the choices a reviewer is expected to
#: contest, which is why they are one table rather than six branches.
HEADER_CATEGORY_RULES: tuple[tuple[re.Pattern[str], NrrCategory], ...] = (
    # a field / attribute sheet: 字段、类型、说明 and their English spellings
    (
        re.compile(
            r"^(field|fieldname|column|attributename|attribute|property|dataelement|"
            r"字段|属性|列名|域名)$"
        ),
        NrrCategory.DATA_REQUIREMENT,
    ),
    (
        re.compile(
            r"(^|_)(field|fields|attribute|attributes|dataelement|column|property)(_|$)"
            r"|(字段|属性|列名)$"
        ),
        NrrCategory.DATA_REQUIREMENT,
    ),
    # an option / enumeration sheet (OPTION_HEADER_RULES recognises the code+name shape)
    (
        re.compile(
            r"^(option|optioncode|optionname|code|enum|value|list|选项|选项码|选项名称|代码|值)$"
        ),
        NrrCategory.DATA_REQUIREMENT,
    ),
    # organisation / facility / unit hierarchy lists
    (
        re.compile(
            r"(organis|organiza|orgunit|orgcode|orgname|facility|district|catchment|"
            r"hierarch|parent|tier|机构|组织|单位|区域|层级|分级|^unit|^office|^site)"
        ),
        NrrCategory.ORGANISATION,
    ),
    # a sheet of permission requirements
    (
        re.compile(r"(access|permission|privilege|sharing|authority|权限|访问)"),
        NrrCategory.ACCESS,
    ),
    # reporting and analysis requirements
    (re.compile(r"(report|dashboard|analytic|indicator|指标|报表|分析)"), NrrCategory.REPORT),
    # notification requirements
    (re.compile(r"(notification|remind|alert|sms|推送|通知|提醒)"), NrrCategory.NOTIFICATION),
    # multilingual requirements
    (re.compile(r"(i18n|translat|locale|language|多语言|翻译|语言)"), NrrCategory.I18N),
    # process / workflow sheets
    (
        re.compile(r"(process|workflow|stage|step|流程|步骤|状态)"),
        NrrCategory.BUSINESS_PROCESS,
    ),
    # rule sheets
    (re.compile(r"(rule|constraint|validation|policy|规则|约束|校验)"), NrrCategory.BUSINESS_RULE),
    # who does the work
    (re.compile(r"^(actor|role|participant|user|job|参与者|角色|岗位)$"), NrrCategory.PARTICIPANT),
    # object / entity sheets
    (re.compile(r"^(entity|object|class|model|实体|对象|类)$"), NrrCategory.BUSINESS_ENTITY),
    # goals
    (re.compile(r"(goal|objective|successmetric|目标|目的)"), NrrCategory.BUSINESS_GOAL),
    # a column whose name is an action phrase is the use-case sheet of AI-INP-002
    (
        re.compile(
            r"^(usecase|userstory|action|activity|operation|task|function|用例|操作|活动|功能)$"
        ),
        NrrCategory.USE_CASE,
    ),
)

#: The code+name pair that makes a table an *option list* rather than a field list.
OPTION_HEADER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(code|optioncode|选项码|编码|代码|值|value|key)$"), "code"),
    (re.compile(r"^(name|optionname|选项名称|名称|label|displayname)$"), "name"),
)

#: A header naming an action column, and the verb phrase that makes a *cell* an action.
#: The cell reading is the fallback when no header rule matched: a sheet of "user does X"
#: rows has no standard column name, and AI-INP-006 says to propose, not to guess.
ACTION_HEADER_PATTERN = re.compile(
    r"^(usecase|action|activity|operation|task|step|用例|操作|活动|功能)$"
)
ACTION_VERB_PATTERN = re.compile(
    r"^("
    r"create|register|record|submit|approve|reject|review|assign|collect|verify|validate|"
    r"export|import|notify|manage|search|edit|delete|update|print|sign|send|receive|"
    r"新建|登记|录入|提交|审核|审批|退回|分派|采集|校验|导出|导入|通知|查询|修改|删除|打印|签发"
    r")"
)

#: A column that groups options into one named list (DHIS2's "name of the option set").
SET_HEADER_PATTERN = re.compile(r"^(optionset|list|set|group|选项集|集合|名称集合)$")

#: How many column values one statement may quote before it is summarised (S2 caps a
#: statement at 4096 characters; this keeps the *common* case readable instead of clipped).
STATEMENT_VALUE_LIMIT = 12

#: How many characters of one cell value a statement quotes.
STATEMENT_CELL_LIMIT = 200

#: category → the words an NRR statement begins with, so a Blueprint reader can see which
#: of AI-INP-005's thirteen categories a row was placed in.
CATEGORY_PHRASES: Mapping[NrrCategory, str] = {
    NrrCategory.BUSINESS_GOAL: "Business goal",
    NrrCategory.PARTICIPANT: "Participant",
    NrrCategory.BUSINESS_ENTITY: "Business entity",
    NrrCategory.USE_CASE: "Use case",
    NrrCategory.BUSINESS_PROCESS: "Process step",
    NrrCategory.BUSINESS_RULE: "Business rule",
    NrrCategory.DATA_REQUIREMENT: "Data requirement",
    NrrCategory.ORGANISATION: "Organisation unit",
    NrrCategory.ACCESS: "Access requirement",
    NrrCategory.REPORT: "Report requirement",
    NrrCategory.NOTIFICATION: "Notification requirement",
    NrrCategory.I18N: "Multilingual requirement",
    NrrCategory.OPEN_QUESTION: "Open question",
}

#: The reason a fragment with a proposed mapping carries: naming the requirement keeps the
#: review obligation in the artifact rather than only in a question a UI may not show yet.
REVIEW_REASON = (
    "the column mapping was not supplied, so it is proposed and needs confirmation "
    "(AI-INP-006)"
)


def normalise_header(value: object) -> str:
    """NFKC, lowercased, non-alphanumeric runs collapsed to ``_`` (AI-INP-006 matching).

    CJK characters are alphanumeric to Python and are kept: a Chinese header is matched as
    itself, never transliterated away.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "_", text).strip("_")


def category_for_header(name: object) -> NrrCategory | None:
    """The category one header says for itself, or None when it says nothing."""
    normalised = normalise_header(name)
    if not normalised:
        return None
    for pattern, category in HEADER_CATEGORY_RULES:
        if pattern.match(normalised):
            return category
    return None


def _rule_rank(category: NrrCategory) -> int:
    for index, (_pattern, candidate) in enumerate(HEADER_CATEGORY_RULES):
        if candidate is category:
            return index
    return len(HEADER_CATEGORY_RULES)


def _matched(headers: Sequence[str], pattern: re.Pattern[str]) -> int | None:
    for index, header in enumerate(headers):
        if pattern.match(normalise_header(header)):
            return index
    return None


@dataclass(frozen=True)
class TablePlan:
    """What a header row says about a table — computed once, reused for every row."""

    category: NrrCategory
    headers: tuple[str, ...]
    label_column: int | None
    detail_columns: tuple[int, ...]
    option_code_column: int | None = None
    option_name_column: int | None = None
    set_column: int | None = None
    #: ``(raw header, normalised header, category value)`` — the proposal shown to a reviewer
    matches: tuple[tuple[str, str, str], ...] = ()
    is_option_list: bool = False
    recognised_by: str = "header rule"

    def proposed_mapping(self) -> dict[str, str]:
        """The header mapping this parser would apply, in AI-INP-006's words.

        The proposed target is the normalised header name when no ``column_mapping``
        renamed it: what a reviewer is being asked to confirm is *which column means what*,
        and the table's category is what says that.
        """
        out: dict[str, str] = {}
        for index, (raw, normalised, _category) in enumerate(self.matches):
            key = raw or f"column{index + 1}"
            out[key] = normalised or f"column{index + 1}"
        return out

    def rendered(self) -> str:
        """The proposal in the form a human reads and a caller can copy back verbatim."""
        return ", ".join(
            f"'{raw or f'column{index + 1}'}' -> '{name}'"
            + (" (label column)" if index == self.label_column else "")
            + (f" as {category}" if category else "")
            for index, (raw, name, category) in enumerate(self.matches)
        )


def effective_headers(raw_headers: Sequence[object], mapping: Mapping[str, Any] | None) -> list[str]:
    """Apply ``column_mapping``: a mapped name wins, an unmapped column keeps its own.

    Keys are tried as the raw header, then as the 1-based position (for ``has_header=False``
    sheets), then as the normalised header, so a caller may write any of the three.
    """
    headers = [str(header if header is not None else "") for header in raw_headers]
    if not mapping:
        return headers
    by_normalised = {
        normalise_header(key): str(value) for key, value in mapping.items() if key is not None
    }
    out: list[str] = []
    for index, header in enumerate(headers):
        candidate: Any = mapping.get(header)
        if candidate is None:
            candidate = mapping.get(str(index + 1))
        if candidate is None:
            candidate = by_normalised.get(normalise_header(header))
        out.append(str(candidate) if candidate is not None else header)
    return out


def option_columns(headers: Sequence[str]) -> tuple[int | None, int | None, int | None] | None:
    """The code+name shape of an option/enum list, or None when the sheet is not one."""
    code_column: int | None = None
    name_column: int | None = None
    for index, header in enumerate(headers):
        outcome = _matches_any(OPTION_HEADER_RULES, normalise_header(header))
        if outcome == "code" and code_column is None:
            code_column = index
        elif outcome == "name" and name_column is None:
            name_column = index
    if code_column is None or name_column is None:
        return None
    #: a field sheet that also carries a type column is a field sheet, not an option list
    if any(normalise_header(header) in {"type", "datatype", "类型", "字段类型"} for header in headers):
        return None
    return code_column, name_column, _matched(headers, SET_HEADER_PATTERN)


def _matches_any(rules: tuple[tuple[re.Pattern[str], str], ...], normalised: str) -> str | None:
    for pattern, outcome in rules:
        if pattern.match(normalised):
            return outcome
    return None


def analyse_table(
    headers: Sequence[str], rows: Sequence[Sequence[Any]] = ()
) -> TablePlan | None:
    """Classify a table from its (already mapped) header names, then from its values.

    None means the parser cannot say what the table is about: the caller then asks rather
    than picking a category at random (AI-INP-008).
    """
    matches: list[tuple[str, str, str]] = []
    columns: list[tuple[int, NrrCategory]] = []
    for index, header in enumerate(headers):
        category = category_for_header(header)
        matches.append(
            (
                str(header if header is not None else ""),
                normalise_header(header),
                category.value if category is not None else "",
            )
        )
        if category is not None:
            columns.append((index, category))

    options = option_columns(headers)
    if options is not None:
        code_column, name_column, set_column = options
        excluded = {index for index in (code_column, name_column, set_column) if index is not None}
        return TablePlan(
            category=NrrCategory.DATA_REQUIREMENT,
            headers=tuple(headers),
            label_column=name_column if name_column is not None else code_column,
            detail_columns=tuple(index for index in range(len(headers)) if index not in excluded),
            option_code_column=code_column,
            option_name_column=name_column,
            set_column=set_column,
            matches=tuple(matches),
            is_option_list=True,
            recognised_by="option code+name header pair",
        )

    #: a column *named* like an action speaks for the sheet; if nothing else matched, a
    #: column whose every value *reads* like an action does
    action_header = _matched(headers, ACTION_HEADER_PATTERN)
    value_column: int | None = None
    if not columns:
        value_column = _action_value_column(headers, rows)
        if value_column is not None:
            columns = [(value_column, NrrCategory.USE_CASE)]
    if not columns:
        return None

    #: rule priority first, column order second: a 字段/类型/说明 sheet is a data
    #: requirement whatever else its columns look like
    label_index, category = min(columns, key=lambda item: (_rule_rank(item[1]), item[0]))
    if action_header is not None and category is NrrCategory.USE_CASE:
        label_index = action_header
    #: every other column quotes its value into the statement, because a reviewer reads
    #: the NRR, not the sheet: 类型 and 说明 are the content of a field list, not noise
    details = tuple(index for index in range(len(headers)) if index != label_index)
    recognised_by = "header rule"
    if category is NrrCategory.USE_CASE and action_header is not None:
        recognised_by = "action header"
    elif category is NrrCategory.USE_CASE and value_column is not None:
        recognised_by = "action column values"
    return TablePlan(
        category=category,
        headers=tuple(headers),
        label_column=label_index,
        detail_columns=details,
        set_column=_matched(headers, SET_HEADER_PATTERN),
        matches=tuple(matches),
        recognised_by=recognised_by,
    )


def _action_value_column(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> int | None:
    """The first column whose *values* all read as an action verb phrase.

    AI-INP-002's "字段/选项/组织清单" are named by their headers, but a use-case sheet is
    usually just a list of things a person does; when every value of one column starts
    with an action verb, that column is the use case.
    """
    for index in range(len(headers)):
        values = [_text(_cell(row, index)) for row in rows]
        material = [value for value in values if value]
        if not material:
            continue
        if all(ACTION_VERB_PATTERN.match(value.strip().lower()) for value in material):
            return index
    return None


class TabularCollector:
    """Row → entry, shared by the CSV and the XLSX parser.

    The two formats differ only in *where* a row is, so ``location_of`` is the seam and the
    header rules, the statement wording and the business-key rules are written once.  Each
    material row arrives already numbered as it appears in the file.
    """

    def __init__(
        self,
        *,
        fragment: Fragment,
        sheet: str,
        plan: TablePlan,
        status: NrrStatus,
        source_artifact_id: str,
        location_of: Callable[[int, int], Location],
        keys: list[str] | None = None,
    ) -> None:
        self.fragment = fragment
        self.sheet = sheet
        self.plan = plan
        self.status = status
        self.source_artifact_id = source_artifact_id
        self.location_of = location_of
        #: shared across the sheets of one workbook so a businessKey stays unique per
        #: container (S2's rule), not merely unique per sheet
        self.keys: list[str] = keys if keys is not None else []

    def collect(self, rows: Sequence[tuple[int, Sequence[Any]]]) -> None:
        for row_number, row in rows:
            self.add_row(row_number, row)

    def add_row(self, row_number: int, row: Sequence[Any]) -> None:
        label, label_column = self._label(row, row_number)
        payload = self._payload(row)
        location = self.location_of(row_number, label_column)
        entry = NrrEntry(
            category=self.plan.category,
            business_key=disambiguate(
                self.keys, business_key(self.plan.category, label), seed=str(location)
            ),
            statement=self._statement(row, label, payload),
            location=location,
            #: 1.0 because the extraction is a measured fact (AI-INP-010); what is not yet
            #: confirmed is the mapping, and the CANDIDATE status says exactly that
            confidence=1.0,
            status=self.status,
            source_artifact_id=self.source_artifact_id,
            rationale=(
                None
                if self.status is NrrStatus.CONFIRMED
                else "column mapping proposed by header matching, not yet confirmed (AI-INP-006)"
            ),
            payload=payload,
        )
        self.keys.append(entry.business_key)
        self.fragment.entries.append(entry)

    def _label(self, row: Sequence[Any], row_number: int) -> tuple[str, int]:
        column = self.plan.label_column if self.plan.label_column is not None else 0
        text = _text(_cell(row, column))
        if text:
            return text, column
        #: a row with no label is still a row of the input; the sheet and row number say
        #: which, rather than the parser inventing a name
        return f"{self.sheet} row {row_number}", column

    def _payload(self, row: Sequence[Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"sheet": self.sheet}
        if self.plan.is_option_list:
            option = {
                "code": _text(_cell(row, self.plan.option_code_column)) or None,
                "name": _text(_cell(row, self.plan.option_name_column)) or None,
            }
            payload["options"] = [option] if option["code"] or option["name"] else []
            set_name = _text(_cell(row, self.plan.set_column))
            if set_name:
                payload["optionSet"] = set_name
        values: dict[str, Any] = {}
        for index, header in enumerate(self.plan.headers):
            if index == self.plan.label_column or index in {
                self.plan.option_code_column,
                self.plan.option_name_column,
                self.plan.set_column,
            }:
                continue
            if index >= len(row):
                continue
            value = _scalar(row[index])
            if value is None or value == "":
                continue
            values[header or f"column{index + 1}"] = value
        if values:
            payload["values"] = values
        return payload

    def _statement(self, row: Sequence[Any], label: str, payload: Mapping[str, Any]) -> str:
        phrase = CATEGORY_PHRASES[self.plan.category]
        details: list[str] = []
        if self.plan.is_option_list:
            options = payload.get("options") or []
            option = options[0] if options else {}
            code = option.get("code")
            if code and code != label:
                details.append(f"code '{code}'")
            set_name = payload.get("optionSet")
            if set_name:
                details.append(f"of the option list '{set_name}'")
        for index in self.plan.detail_columns:
            value = _text(_cell(row, index))
            if not value:
                continue
            header = self.plan.headers[index] if index < len(self.plan.headers) else ""
            details.append(f"{header or f'column{index + 1}'}: {_clip(value)}")
        head = f"{phrase} '{label}'"
        if not details:
            return f"{head} is listed in the input sheet '{self.sheet}'."
        return f"{head} — " + "; ".join(details[:STATEMENT_VALUE_LIMIT]) + "."


def _cell(row: Sequence[Any], column: int | None) -> Any:
    if column is None or column < 0 or column >= len(row):
        return None
    return row[column]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(_scalar(value)).strip()


def _scalar(value: object) -> Any:
    """Keep a cell JSON-serialisable without inventing a format for it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _clip(value: str, limit: int = STATEMENT_CELL_LIMIT) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def sheet_name(options: Mapping[str, Any], *, default: str) -> str:
    """The sheet part of a location: an explicit name, the file basename, or ``default``."""
    explicit = options.get("sheet")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    filename = options.get("filename")
    if isinstance(filename, str) and filename.strip():
        stem = filename.replace("\\", "/").rsplit("/", 1)[-1]
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        if stem.strip():
            return stem.strip()
    return default


def mapping_of(options: Mapping[str, Any], sheet: str | None = None) -> dict[str, Any]:
    """The supplied ``column_mapping`` for one sheet, or an empty dict when none was given.

    A workbook whose worksheets are different tables needs a mapping *per sheet*
    (AI-INP-006 asks one question per sheet, so an answer is per sheet too).  Two spellings
    are accepted: the flat ``{"字段": "name"}``, which applies to every sheet, and the nested
    ``{"字段清单": {"字段": "name"}}``, which applies to one.  A sheet-specific answer must
    not be applied to a sheet it was never proposed for — that would confirm a mapping a
    reviewer never saw.
    """
    mapping = options.get("column_mapping") or options.get("columnMapping")
    if not isinstance(mapping, Mapping):
        return {}
    if sheet is not None:
        scoped = mapping.get(sheet)
        if isinstance(scoped, Mapping):
            return {str(key): str(value) for key, value in scoped.items()}
        # nested but not for this sheet: nothing was confirmed about this one
        if any(isinstance(value, Mapping) for value in mapping.values()):
            return {}
    return {
        str(key): str(value)
        for key, value in mapping.items()
        if not isinstance(value, Mapping)
    }


def mapping_text(
    sheet: str,
    headers: Sequence[str],
    plan: TablePlan | None,
    *,
    unmatched: bool,
) -> str:
    """The one AI-INP-006 question, in the form both halves of the gate ask it in.

    One function holds the wording, so the ``openQuestions`` record written into the NRR and
    the ``Question`` S1 shows a human cannot say two different things about one mapping.
    """
    if unmatched or plan is None:
        listed = ", ".join(f"'{header or '(blank)'}'" for header in headers)
        return (
            f"No column of sheet '{sheet}' matched a deterministic header rule "
            f"({listed}). Which target field should each column map to? Supply "
            "options.column_mapping and re-parse."
        )
    return (
        f"No column mapping was supplied for sheet '{sheet}'. The parser proposes "
        f"{plan.rendered()}, reading the sheet as {plan.category.value} requirements. Confirm "
        "or correct the mapping and re-parse with options.column_mapping."
    )


def mapping_question(
    sheet: str,
    headers: Sequence[str],
    plan: TablePlan | None,
    *,
    unmatched: bool,
) -> dict[str, Any]:
    """The minimal ``openQuestions`` record (S2's schema allows ``question`` and ``severity``)."""
    return {
        "question": mapping_text(sheet, headers, plan, unmatched=unmatched),
        "severity": QuestionSeverity.HIGH.value,
    }


def apply_mapping_gate(
    fragment: Fragment,
    *,
    sheet: str,
    headers: Sequence[str],
    plan: TablePlan | None,
    unmatched: bool,
    affected_keys: Sequence[str],
    per_sheet: bool,
) -> None:
    """Mark a fragment whose mapping is only *proposed*: review, question, proposal (AI-INP-006).

    ``per_sheet`` is the one difference between the two tabular formats a reviewer can see:
    a CSV has one unnamed sheet, so its proposal is recorded flat, while a workbook records
    one proposal per worksheet because two sheets of one file can be two different tables.
    :func:`mapping_review_questions` reads either form back.
    """
    text = mapping_text(sheet, headers, plan, unmatched=unmatched)
    fragment.needs_review = True
    fragment.review_reason = REVIEW_REASON
    fragment.open_questions.append({"question": text, "severity": QuestionSeverity.HIGH.value})
    proposal = dict(sorted(plan.proposed_mapping().items())) if plan is not None else {}
    if per_sheet:
        existing = fragment.notes.get("proposedColumnMapping")
        by_sheet = dict(existing) if isinstance(existing, dict) else {}
        by_sheet[sheet] = proposal
        fragment.notes["proposedColumnMapping"] = by_sheet
    else:
        fragment.notes["proposedColumnMapping"] = proposal
    fragment.notes["recognisedBy"] = plan.recognised_by if plan is not None else "nothing"
    #: the structured record of this gate, so the Question S1 shows can be rebuilt from the
    #: fragment alone: the wording is stored once and the two halves cannot drift
    reviews = [item for item in (fragment.notes.get("mappingReview") or []) if isinstance(item, dict)]
    reviews.append(
        {
            "sheet": sheet,
            "question": text,
            "proposal": proposal,
            "headers": [str(header) for header in headers],
            "affectedKeys": sorted(set(affected_keys)),
            # one basis per sheet: two worksheets of one workbook can be two different
            # tables, and AI-INP-014 reports the reason for each of them
            "recognisedBy": plan.recognised_by if plan is not None else "nothing",
            "category": plan.category.value if plan is not None else None,
        }
    )
    fragment.notes["mappingReview"] = sorted(reviews, key=lambda item: str(item.get("sheet")))


def mapping_review_questions(fragment: Fragment) -> list[dict[str, Any]]:
    """The AI-INP-006 Questions S1 shows a human, one per sheet, each with its suggestions.

    A suggestion carries the mapping in the spelling ``options.column_mapping`` takes, so
    answering it is executable: S1 records which suggestion was chosen, the caller re-parses
    with that mapping and every ``CANDIDATE`` of this fragment becomes ``CONFIRMED``
    (AI-INP-006's second half).  "Keep the file's own order" is offered alongside because a
    sheet whose columns are already in the order the parser wants needs no mapping at all.
    """
    reviews = fragment.notes.get("mappingReview")
    if not isinstance(reviews, list):
        return []
    out: list[dict[str, Any]] = []
    for record in reviews:
        if not isinstance(record, dict):
            continue
        sheet = str(record.get("sheet") or "sheet")
        proposal = record.get("proposal")
        mapping = (
            {str(key): str(value) for key, value in proposal.items()}
            if isinstance(proposal, Mapping)
            else {}
        )
        keys = [str(key) for key in (record.get("affectedKeys") or [])]
        suggestions: list[dict[str, Any]] = []
        if mapping:
            rendered = ", ".join(f"'{key}' -> '{value}'" for key, value in sorted(mapping.items()))
            suggestions.append(
                {
                    "id": "S1",
                    "text": f"Use the proposed mapping for '{sheet}': {rendered}",
                    "rationale": (
                        f"the parser matched the headers of sheet '{sheet}' by "
                        f"{fragment.notes.get('recognisedBy')}; re-parsing with this mapping "
                        "confirms the entries it produced"
                    ),
                    "changesUid": False,
                    "regenerates": False,
                    "columnMapping": dict(sorted(mapping.items())),
                }
            )
        suggestions.append(
            {
                "id": f"S{len(suggestions) + 1}",
                "text": f"Keep the file's own order for '{sheet}' — no column mapping",
                "rationale": (
                    "the sheet is read as it stands, in the order its columns appear; the "
                    "entries stay CANDIDATE because nothing was confirmed about their meaning"
                ),
                "changesUid": False,
                "regenerates": False,
                "columnMapping": {},
            }
        )
        out.append(
            {
                "text": str(record.get("question") or ""),
                "severity": QuestionSeverity.HIGH.value,
                "blocking": True,
                "affects": [f"{AFFECTS_PREFIX}{key}" for key in keys[:AFFECTS_LIMIT]],
                "suggestions": suggestions,
                "details": {
                    "sheet": sheet,
                    "headers": [str(header) for header in (record.get("headers") or [])],
                    "proposedColumnMapping": dict(sorted(mapping.items())),
                    "artifactId": fragment.source_artifact_id,
                },
            }
        )
    return out


__all__ = [
    "CATEGORY_PHRASES",
    "HEADER_CATEGORY_RULES",
    "OPTION_HEADER_RULES",
    "ACTION_HEADER_PATTERN",
    "ACTION_VERB_PATTERN",
    "SET_HEADER_PATTERN",
    "STATEMENT_VALUE_LIMIT",
    "STATEMENT_CELL_LIMIT",
    "AFFECTS_LIMIT",
    "REVIEW_REASON",
    "TabularCollector",
    "TablePlan",
    "analyse_table",
    "apply_mapping_gate",
    "category_for_header",
    "effective_headers",
    "mapping_of",
    "mapping_question",
    "mapping_review_questions",
    "mapping_text",
    "normalise_header",
    "option_columns",
    "sheet_name",
]
