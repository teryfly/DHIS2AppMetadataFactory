"""Error-message localisation, Simplified Chinese and English (CP-NFR-09).

Messages are chosen per request from ``Accept-Language``; the text never carries
internal details — those go in ``details`` verbatim.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh-CN", "en")

#: Labels for the S5IF-FLD01 precondition checklist.  These are not errors: UI-WB-002
#: shows the missing item as written, so the list is localised the same way and must
#: hold the same keys in both languages (CP-NFR-09).
_CHECKLIST_ZH: dict[str, str] = {
    "PRIOR_STAGE_COMPLETE": "前置阶段尚未完成",
    "UPSTREAM_ARTIFACTS": "缺少上游阶段必需的产物",
    "PARSED_INPUT": "还没有已解析的输入文件",
    "CAPABILITY_PROFILE_VALID": "本 Run 尚未记录有效的能力档案",
}

_CHECKLIST_EN: dict[str, str] = {
    "PRIOR_STAGE_COMPLETE": "The preceding stage has not finished",
    "UPSTREAM_ARTIFACTS": "An artifact the upstream stage must produce is missing",
    "PARSED_INPUT": "No input document has been parsed yet",
    "CAPABILITY_PROFILE_VALID": "This run has no recorded capability profile",
}

_ZH: dict[str, str] = {
    "OPERATOR_REQUIRED": "改变状态的操作必须声明操作者，请在请求头 X-Operator 中提供操作人名称。",
    "IDEMPOTENCY_KEY_REQUIRED": "该请求必须在请求头 Idempotency-Key 中提供幂等键；提交（COMMIT）不允许自动重试。",
    "INVALID_STATE": "当前状态不允许该操作。",
    "STATE_NOT_ALLOWED": "当前状态不允许该操作。",
    "PROJECT_ARCHIVED": "项目已归档，处于只读状态，不能执行该操作。",
    "UPLOAD_REJECTED": "上传被拒绝：文件类型或大小不符合限制。",
    "REQUEST_IN_PROGRESS": "相同幂等键的请求仍在处理中，请稍后重试。",
    "IDEMPOTENCY_CONFLICT": "相同幂等键已用于不同的请求内容。",
    "NOT_FOUND": "请求的对象不存在。",
    "GATE_NOT_APPROVED": "审批尚未通过，不能执行该操作。",
    "APPROVAL_INVALIDATED": "你所看到的内容已经变化，本次审批已失效，请刷新后重新审批。",
    "DRIFT_DETECTED": "目标环境自校验以来发生了变化，请先重新执行校验。",
    "ENVIRONMENT_LOCKED": "目标环境已被紧急锁定，禁止提交。",
    "OUTSIDE_COMMIT_WINDOW": "当前时间不在该环境允许的提交时间窗内。",
    "PRIOR_VERIFICATION_MISSING": "该版本尚未在任何非生产环境完成验证，不能提交到生产环境。",
    "BUDGET_EXCEEDED": "成本预算已超限，请调整预算或改用人工编写方式。",
    "POLICY_DENIED": "数据外发策略拒绝了本次操作。",
    "VALIDATION_ERROR": "校验未通过。",
    "SCHEMA_ERROR": "数据结构不符合 Schema 要求。",
    "ARTIFACT_ERROR": "产物操作失败。",
    "REGISTRY_ERROR": "Registry 操作失败。",
    "CONCURRENCY_ERROR": "并发冲突，请基于最新状态重试。",
    "CANCELLED": "操作已取消。",
    "LLM_TIMEOUT": "模型调用超时。",
    "PARSE_INTERRUPTED": "服务重启导致解析中断，请重新解析。",
    "LLM_ERROR": "模型调用失败。",
    "DHIS2_TIMEOUT": "DHIS2 请求超时。",
    "DHIS2_AUTH_ERROR": "DHIS2 认证失败。",
    "DHIS2_PERMISSION_ERROR": "DHIS2 权限不足。",
    "DHIS2_NOT_FOUND": "DHIS2 中未找到该对象。",
    "DHIS2_IMPORT_ERROR": "DHIS2 导入失败。",
    "SECRET_UNAVAILABLE": "密钥引用无法解析：该引用不存在、凭证库未配置主密钥，或与当前主密钥不匹配。",
    "SECRET_IN_USE": "该凭证仍被环境、提供方或模型引用，删除会让那些引用解析不到值；请先改指向别的凭证或改用环境变量引用。",
    "ARTIFACT_PURGED": "该产物内容已被清除。",
    "TRANSACTION_MISUSE": "内部错误：工作单元使用不当。",
    "MIGRATION_ERROR": "内部错误：数据库迁移失败。",
    "INTERNAL_ERROR": "服务内部错误，请联系管理员并提供关联 ID。",
    "PROVIDER_UNAVAILABLE": "所需的子系统当前不可用，请稍后重试。",
    "AI_GOVERNANCE_ACTION_UNKNOWN": "不支持的治理操作。",
    "AI_PROMPT_ACTIVATION_BLOCKED": "缺少通过的评测或回归记录，该 Prompt 不能激活。",
    "AI_PROMPT_NOT_ACTIVATED": "该 Agent 尚未激活任何 Prompt，运行被拒绝；请先走激活链或改用人工编写。",
    "AI_POLICY_RELAXATION_DENIED": "该数据策略放宽被拒绝。",
    "AI_FEATURE_NOT_ENABLED": "该功能尚未在当前的里程碑启用。",
    "AI_BUDGET_INVALID": "预算上限或告警阈值不符合要求：上限须为非负数，阈值须介于 0 与 1 之间，币种须为三位字母代码。",
    "UNAUTHENTICATED": "请求未通过认证，请登录后再试。",
    "CSRF_FAILED": "该请求缺少 CSRF 校验值，请在请求头 X-CSRF-Token 中提供会话的校验令牌。",
    "AI_EVAL_SET_MISSING": "评测集缺失，无法运行评测。",
    "AI_PROVIDER_UNAVAILABLE": "模型供应方当前不可用。",
    "BP_UNSUPPORTED_SCHEMA_VERSION": "不支持的 Blueprint Schema 版本。",
    "BP_FEATURE_NOT_ENABLED": "该 Blueprint 功能尚未在当前的里程碑启用。",
    "BP_IMPORT_CONFLICT": "导入的内容与现有版本冲突，请基于最新版本重试。",
    "BP_DRAFT_NOT_MATERIALIZED": "该草稿尚未准备好基线内容，请先重置后再试。",
    "UID_POOL_EXHAUSTED": "UID 池已耗尽，请补充后重试。",
    "PERMISSION_DENIED": "当前操作者没有执行该操作的权限。",
    "FEATURE_NOT_ENABLED": "该功能属于尚未交付的里程碑，当前版本不可用；details 标明功能与所需里程碑。",
    # --- 转发给子系统的调用所答的码（S5IF-G07.2：一个 provider 的拒绝不能读成 S1 崩溃） ---
    "DHIS2_UNREACHABLE": "无法连接到目标 DHIS2 实例，请检查地址与网络可达性后重试。",
    "DHIS2_RESPONSE_INVALID": "目标 DHIS2 实例返回了无法解析的响应。",
    "DHIS2_TASK_LOST": "目标 DHIS2 实例上的导入任务记录已丢失，无法确认结果，请核对实例状态后重试。",
    "DHIS2_TASK_FAILED": "目标 DHIS2 实例拒绝了这次导入，details 里是实例自己的报错。",
    "DHIS2_TLS_INVALID": "该环境的 TLS 设置与地址不匹配，请检查证书或调整「要求 TLS」开关。",
    "DHIS2_VERSION_UNSUPPORTED": "目标实例的版本不在已支持的 Profile 范围内，无法执行该操作。",
    "DHIS2_PROFILE_STALE": "该环境的能力档案已过期，请重新探测后再试。",
    "DX_PRODUCTION_PROTECTED": "生产环境的保护规则拒绝了这次操作。",
    "DX_DELETION_FORBIDDEN": "该环境的删除策略禁止删除对象。",
    "DX_PAYLOAD_HASH_MISMATCH": "校验产物、审批记录与当前包的三方载荷哈希不一致，提交被拒绝。",
    "DX_VALIDATION_STALE": "校验记录早于待提交的包，请重新校验后再提交。",
    "AI_OUTPUT_INVALID": "模型返回的内容不可用，请重试或改用人工编写。",
    "AI_CONTEXT_TOO_LARGE": "送给模型的上下文超出限制，请缩小范围后重试。",
    "SCANNED_DOCUMENT_UNSUPPORTED": "这份扫描文档不属于当前支持的范围。",
    "AI_PATCH_REJECTED": "模型提出的这次修改被拒绝。",
    "AI_PARSER_DEPENDENCY_MISSING": "解析所需的组件未安装，当前部署不具备该能力。",
    "AI_PARSER_INPUT_INVALID": "这份上传文件读不出来，details 里是解析器的报告。",
    "AI_PARSER_UNMATCHED": "没有解析器能处理这种文件类型。",
    "AI_CONSTRAINT_VIOLATION": "模型的输出结构合法，但违反了该 Agent 的禁止性约束。",
    "BP_EXPRESSION_GRAMMAR_MISSING": "编译表达式所需的语法组件未安装，当前部署不具备该能力。",
}

_EN: dict[str, str] = {
    "OPERATOR_REQUIRED": "This operation must declare an operator via the X-Operator header.",
    "IDEMPOTENCY_KEY_REQUIRED": (
        "This request must carry an Idempotency-Key header; a COMMIT is never retried automatically."
    ),
    "INVALID_STATE": "The requested operation is not allowed in the current state.",
    "STATE_NOT_ALLOWED": "The current state of this change request does not allow that step.",
    "PROJECT_ARCHIVED": "The project is archived and read-only.",
    "UPLOAD_REJECTED": "Upload rejected: file type or size is outside the allowed limits.",
    "REQUEST_IN_PROGRESS": "A request with the same idempotency key is still in progress.",
    "IDEMPOTENCY_CONFLICT": "The idempotency key was already used with a different request body.",
    "NOT_FOUND": "The requested object does not exist.",
    "GATE_NOT_APPROVED": "The required approval has not been granted.",
    "APPROVAL_INVALIDATED": "The content you reviewed has changed; this approval is no longer valid.",
    "DRIFT_DETECTED": "The target environment changed since validation; please validate again.",
    "ENVIRONMENT_LOCKED": "The target environment is emergency-locked; commits are refused.",
    "OUTSIDE_COMMIT_WINDOW": "The current time is outside the allowed commit window.",
    "PRIOR_VERIFICATION_MISSING": "This version has not been verified in a non-production environment.",
    "BUDGET_EXCEEDED": "The cost budget has been exceeded.",
    "POLICY_DENIED": "The data-exfiltration policy denied this operation.",
    "VALIDATION_ERROR": "Validation failed.",
    "SCHEMA_ERROR": "The payload does not conform to its schema.",
    "ARTIFACT_ERROR": "The artifact operation failed.",
    "REGISTRY_ERROR": "The registry operation failed.",
    "CONCURRENCY_ERROR": "Concurrent modification detected; retry against the latest state.",
    "CANCELLED": "The operation was cancelled.",
    "LLM_TIMEOUT": "The model call timed out.",
    "PARSE_INTERRUPTED": "Parsing was interrupted by a service restart; parse the input again.",
    "LLM_ERROR": "The model call failed.",
    "DHIS2_TIMEOUT": "The DHIS2 request timed out.",
    "DHIS2_AUTH_ERROR": "DHIS2 authentication failed.",
    "DHIS2_PERMISSION_ERROR": "DHIS2 permission denied.",
    "DHIS2_NOT_FOUND": "The object was not found in DHIS2.",
    "DHIS2_IMPORT_ERROR": "The DHIS2 import failed.",
    "SECRET_UNAVAILABLE": "The secret reference could not be resolved: the name does not exist, no master key is configured, or the stored value was sealed under a different key.",
    "SECRET_IN_USE": "An environment, provider or model still references this credential; move those references first.",
    "ARTIFACT_PURGED": "The artifact content has been purged.",
    "TRANSACTION_MISUSE": "Internal error: unit of work misuse.",
    "MIGRATION_ERROR": "Internal error: database migration failed.",
    "INTERNAL_ERROR": "Internal server error; report the correlation id.",
    "PROVIDER_UNAVAILABLE": "The required subsystem is unavailable; please retry later.",
    "AI_GOVERNANCE_ACTION_UNKNOWN": "Unsupported governance action.",
    "AI_PROMPT_ACTIVATION_BLOCKED": "A passing evaluation or regression record is required before activation.",
    "AI_PROMPT_NOT_ACTIVATED": "No prompt is activated for this agent, so the run was refused; activate one or author the stage by hand.",
    "AI_POLICY_RELAXATION_DENIED": "That data-policy relaxation was refused.",
    "AI_FEATURE_NOT_ENABLED": "This feature is not enabled at the current milestone.",
    "AI_BUDGET_INVALID": "A budget limit must be a non-negative number or null, thresholds must lie strictly between 0 and 1, and the currency must be a three-letter code.",
    "UNAUTHENTICATED": "The request is not authenticated; sign in first.",
    "CSRF_FAILED": "The request carries no CSRF value; send the session token in the X-CSRF-Token header.",
    "AI_EVAL_SET_MISSING": "The evaluation set is missing; the evaluation cannot run.",
    "AI_PROVIDER_UNAVAILABLE": "The model provider is currently unavailable.",
    "BP_UNSUPPORTED_SCHEMA_VERSION": "Unsupported Blueprint schema version.",
    "BP_FEATURE_NOT_ENABLED": "This Blueprint feature is not enabled at the current milestone.",
    "BP_IMPORT_CONFLICT": "The imported content conflicts with an existing version; retry against the latest.",
    "BP_DRAFT_NOT_MATERIALIZED": "The draft has no base content yet; retry after it is prepared.",
    "UID_POOL_EXHAUSTED": "The UID pool is exhausted; replenish it and retry.",
    "PERMISSION_DENIED": "The operator is not permitted to perform this action.",
    "FEATURE_NOT_ENABLED": "This feature belongs to a milestone that has not been delivered; details name the feature and its milestone.",
    # --- codes a forwarded call answers with (S5IF-G07.2: a provider refusal must not read as an
    # S1 crash) ---
    "DHIS2_UNREACHABLE": "The target DHIS2 instance could not be reached; check its address and reachability, then retry.",
    "DHIS2_RESPONSE_INVALID": "The target DHIS2 instance answered with something that could not be parsed.",
    "DHIS2_TASK_LOST": "The import task record disappeared from the target instance; check its state and retry.",
    "DHIS2_TASK_FAILED": "The target instance rejected this import; details carry the instance's own report.",
    "DHIS2_TLS_INVALID": "This environment's TLS setting does not match its address; check the certificate or the TLS requirement.",
    "DHIS2_VERSION_UNSUPPORTED": "The instance's version is outside the supported capability profiles for this operation.",
    "DHIS2_PROFILE_STALE": "This environment's capability profile is out of date; probe it again and retry.",
    "DX_PRODUCTION_PROTECTED": "The production environment's protection rules refused this operation.",
    "DX_DELETION_FORBIDDEN": "This environment's deletion policy forbids deleting objects.",
    "DX_PAYLOAD_HASH_MISMATCH": "The validated artifact, the approval and the current package do not carry the same payload hash; the commit was refused.",
    "DX_VALIDATION_STALE": "The validation record is older than the package being committed; validate again first.",
    "AI_OUTPUT_INVALID": "The model's answer could not be used; retry or author the content by hand.",
    "AI_CONTEXT_TOO_LARGE": "The context sent to the model exceeds its limit; narrow the request and retry.",
    "SCANNED_DOCUMENT_UNSUPPORTED": "This scanned document is outside what the current deployment supports.",
    "AI_PATCH_REJECTED": "The change the model proposed was rejected.",
    "AI_PARSER_DEPENDENCY_MISSING": "A component needed for parsing is not installed, so this deployment cannot offer that capability.",
    "AI_PARSER_INPUT_INVALID": "The uploaded file could not be read; details carry the parser's report.",
    "AI_PARSER_UNMATCHED": "No parser handles this kind of file.",
    "AI_CONSTRAINT_VIOLATION": "The model's answer was well-formed but broke a prohibition this agent enforces.",
    "BP_EXPRESSION_GRAMMAR_MISSING": "A component needed to compile expressions is not installed, so this deployment cannot offer that capability.",
}

_CATALOGS: dict[str, dict[str, str]] = {"zh-CN": _ZH, "en": _EN}
_CHECKLISTS: dict[str, dict[str, str]] = {"zh-CN": _CHECKLIST_ZH, "en": _CHECKLIST_EN}


def negotiate_language(accept_language: str | None) -> str:
    """Pick a supported tag from an ``Accept-Language`` header value."""
    if not accept_language:
        return DEFAULT_LANGUAGE
    for raw in accept_language.split(","):
        tag = raw.split(";")[0].strip()
        if not tag:
            continue
        if tag in _CATALOGS:
            return tag
        if tag.lower().startswith("zh"):
            return "zh-CN"
        if tag.lower().startswith("en"):
            return "en"
    return DEFAULT_LANGUAGE


def message_for(error_code: str, language: str | None = None, fallback: str | None = None) -> str:
    """Localised message for an error code.

    ``fallback`` (the message the raiser supplied) is used when the code has no
    catalogue entry, so a subsystem-provided English message is never lost.
    """
    lang = language if language in _CATALOGS else DEFAULT_LANGUAGE
    return _CATALOGS[lang].get(error_code) or fallback or error_code


def checklist_label(key: str, language: str | None = None) -> str:
    """The label of one S5IF-FLD01 precondition, in the request's language.

    An unknown key comes back as itself rather than as a translated guess: a checklist
    item S1 did not define is a defect in S1, and showing the key is what makes it
    visible instead of silently wrong.
    """
    lang = language if language in _CHECKLISTS else DEFAULT_LANGUAGE
    return _CHECKLISTS[lang].get(key) or key


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "checklist_label",
    "message_for",
    "negotiate_language",
]
