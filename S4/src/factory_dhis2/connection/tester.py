"""连接测试（DX-CON-001…004、流程 6.1 第 2 步）。

连接测试的价值全在于它的**结论可信**，因此三条硬规矩：

1. 只用只读请求——读 ``me`` 的授权、角色与组织范围，读目录里的 GET 端点看是否被拒。
   绝不“先试写一条看看”（DX-CON-003、DX-AC-01）：测试改变目标环境是不可接受的。
2. 按**调用方声明的需要**检查 Tracker / DataStore / 应用上传范围（总体 SRS 14.2“后四项按
   调用方指定的需要范围检查”），不需要的范围报 SKIPPED 而不是 PASSED。
3. 结果持久化并写审计（DX-CON-004）：审批人看到的“这个环境能用”必须是有日期、可追溯的
   一次事实，而不是每次界面刷新时临时问出来的。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import external_io, idempotency, new_id, structure_hash, unit_of_work
from sqlalchemy import select

from factory_dhis2 import audit
from factory_dhis2.capability import service as capability_service
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.connection import authorities as auth
from factory_dhis2.dto.connection import CheckItem, ConnectionCheck
from factory_dhis2.enums import CheckState, CredentialScope
from factory_dhis2.environment.loader import build_target, require_environment
from factory_dhis2.errors import Dhis2Error, EnvironmentInvalidError, VersionUnsupportedError
from factory_dhis2.models.environment import ConnectionTestRow, EnvironmentRow
from factory_dhis2.profiles import registry
from factory_dhis2.registries import IdemScope

#: DX-CON-001（总体 SRS 14.2）：只有元数据读取总是检查，其余四项按调用方声明的需要检查。
#: 未声明的需要范围报 SKIPPED，不冒充 PASSED。
ALWAYS_CHECKED: tuple[CredentialScope, ...] = (CredentialScope.DISCOVERY,)
REQUIRED_SCOPES: tuple[CredentialScope, ...] = (
    CredentialScope.IMPORT,
    CredentialScope.TRACKER,
    CredentialScope.DATASTORE,
    CredentialScope.APPS,
)
OPTIONAL_SCOPES: tuple[CredentialScope, ...] = REQUIRED_SCOPES + (CredentialScope.MAINTENANCE,)

#: 用于验证“这个范围到底能不能读”的只读探针端点
_READ_PROBES: dict[CredentialScope, tuple[str, dict[str, Any]]] = {
    CredentialScope.DISCOVERY: ("metadata.read", {"dhis2_type": "dataElement"}),
    CredentialScope.IMPORT: ("metadata.read", {"dhis2_type": "dataElement"}),
    CredentialScope.TRACKER: ("tracker.enrollments.get", {"dhis2_type": None}),
    CredentialScope.DATASTORE: ("dataStore", {"dhis2_type": None}),
    CredentialScope.APPS: ("apps", {"dhis2_type": None}),
    CredentialScope.MAINTENANCE: ("system.info", {"dhis2_type": None}),
}


@external_io
def test_connection(
    environment_key: str,
    required_scopes: Sequence[str] | None = None,
    *,
    idempotency_key: str,
    operator: str | None = None,
) -> ConnectionCheck:
    """检查一个环境是否真的可用（SRS 7.1）。"""
    # S0 的幂等存储要求请求摘要是一个规范化结构哈希，不是任意字符串
    digest = structure_hash(
        {
            "operation": "test_connection",
            "environmentKey": environment_key,
            "requiredScopes": sorted(required_scopes or []),
        }
    )
    begun = idempotency.begin(IdemScope.CONNECTION_TEST, idempotency_key, digest)
    if begun.already_completed and begun.first_result:
        return ConnectionCheck.model_validate(begun.first_result)

    with unit_of_work() as session:
        environment = _require_environment(session, environment_key)
        target = build_target(session, environment_key, allow_unprobed=True)
        version_constraint = environment.version_constraint

    checks: list[CheckItem] = []
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)

    # 地址、TLS 与认证必须由**这次失败本身**定性：把不可达或证书无效一律写成 auth 失败，
    # 运维就会去轮换一个本来没问题的令牌（DX-CON-001 要求"每项状态并附说明"）。
    version: str | None = None
    address_state = CheckState.PASSED
    tls_state = CheckState.PASSED if target.tls_required else CheckState.SKIPPED
    auth_state: CheckState | None = None
    auth_detail: str | None = None
    try:
        info = client.call("system.info").payload
        version = str(info.get("version")) if isinstance(info, dict) and info.get("version") else None
    except Dhis2Error as exc:
        if exc.error_code == "DHIS2_TLS_INVALID":
            tls_state = CheckState.FAILED
            auth_state, auth_detail = CheckState.SKIPPED, "证书未通过校验，未能在安全信道上判定认证"
        elif exc.error_code in {"DHIS2_UNREACHABLE", "DHIS2_TIMEOUT"}:
            address_state = CheckState.FAILED
            tls_state = CheckState.SKIPPED
            auth_state, auth_detail = CheckState.SKIPPED, "地址不可达，未发出凭据"
        else:
            auth_state, auth_detail = CheckState.FAILED, f"{exc.error_code}: {exc.message}"[:300]
    else:
        auth_state = CheckState.PASSED

    checks.append(
        CheckItem(
            name="address",
            state=address_state.value,
            detail=target.base_url
            if address_state is CheckState.PASSED
            else f"{target.base_url} 不可达",
        )
    )
    checks.append(
        CheckItem(
            name="tls",
            state=tls_state.value,
            detail=(
                ("证书校验失败" if tls_state is CheckState.FAILED else "环境要求 TLS，证书在请求时校验")
                if target.tls_required
                else "tlsRequired=false"
            ),
        )
    )
    checks.append(
        CheckItem(
            name="auth",
            state=(auth_state or CheckState.PASSED).value,
            detail=auth_detail or ("凭证被实例接受" if auth_state is CheckState.PASSED else None),
            scope="discovery",
        )
    )
    if version:
        constraint_ok = _version_ok(version, version_constraint)
        checks.append(
            CheckItem(
                name="version",
                state=(CheckState.PASSED if constraint_ok else CheckState.FAILED).value,
                detail=f"{version} / 约束 {version_constraint or '无'}",
            )
        )
        profile_ok = True
        try:
            registry.profile_for_version(version)
        except VersionUnsupportedError:
            profile_ok = False
        checks.append(
            CheckItem(
                name="versionProfile",
                state=(CheckState.PASSED if profile_ok else CheckState.FAILED).value,
                detail=(
                    f"{registry.profile_for_version(version).profile_version}"
                    if profile_ok
                    else f"没有覆盖 {version} 的版本 Profile"
                ),
            )
        )
    else:
        checks.append(
            CheckItem(
                name="version",
                state=CheckState.FAILED.value,
                detail="实例未上报版本",
            )
        )

    wanted = _scopes_to_check(required_scopes)
    granted: dict[str, list[str]] = {}
    missing: dict[str, list[str]] = {}
    roles_by_scope: dict[str, list[str]] = {}
    org_scope_by_scope: dict[str, Any] = {}
    for scope in wanted:
        # DX-CON-002 要求报告的是**该凭证范围**的账户实际具备与缺少的权限。用 discovery
        # 账户的授权列表去回答所有范围，等于把只读账户说成有导入权限。
        scoped = client.with_scope(scope)
        authorities, roles, org_scope = _account(scoped)
        if authorities is None:
            checks.append(
                CheckItem(
                    name=f"scope.{scope.value}",
                    state=CheckState.FAILED.value,
                    scope=scope.value,
                    detail="该范围的凭证无法认证或环境未配置该范围的 Secret 引用",
                    missing_authorities=["<unauthenticated service account>"],
                )
            )
            granted[scope.value] = []
            missing[scope.value] = ["<unauthenticated service account>"]
            continue
        report = _check_scope(scoped, scope, authorities)
        checks.append(
            CheckItem(
                name=f"scope.{scope.value}",
                state=(CheckState.PASSED if report.satisfied else CheckState.FAILED).value,
                scope=scope.value,
                detail=report.note,
                missing_authorities=list(report.missing),
            )
        )
        granted[scope.value] = list(report.granted)
        roles_by_scope[scope.value] = list(roles)
        if org_scope:
            org_scope_by_scope[scope.value] = org_scope
        if report.missing:
            missing[scope.value] = list(report.missing)

    discovery_authorities, discovery_roles, discovery_org_scope = _account(client)
    checks.append(
        CheckItem(
            name="account",
            state=(
                CheckState.PASSED if discovery_authorities is not None else CheckState.FAILED
            ).value,
            detail=(
                f"discovery 账户：角色 {len(discovery_roles or [])} 个；授权 "
                f"{len(discovery_authorities or [])} 项；组织范围 {len(discovery_org_scope)} 类"
                "（其余范围的账户事实见各自的 scope.* 检查项）"
            ),
        )
    )
    for scope in OPTIONAL_SCOPES:
        if scope not in wanted:
            checks.append(
                CheckItem(
                    name=f"scope.{scope.value}",
                    state=CheckState.SKIPPED.value,
                    scope=scope.value,
                    detail="调用方未声明该需要范围，未检查（DX-CON-001）",
                )
            )

    ok = address_state is CheckState.PASSED and all(
        item.state != CheckState.FAILED.value for item in checks
    )
    artifact_id: str | None = None
    if ok:
        # 连接通过才合成 Capability Profile：版本、端点可用性都要以实例为准（DX-CAP-003）
        artifact_id = capability_service.probe(
            environment_key, idempotency_key=None, operator=operator
        )

    tested_at = datetime.now(UTC)
    check_documents = [item.model_dump(mode="json") for item in checks]
    with unit_of_work() as session:
        row = ConnectionTestRow(
            id=new_id("dxcnt"),
            environment_key=environment_key,
            tested_at=tested_at,
            dhis2_version=version,
            checks=check_documents,
            missing_authorities=missing,
            granted_authorities=granted,
            result="PASSED" if ok else "FAILED",
            required_scopes=[item.value for item in wanted],
            capability_profile_artifact_id=artifact_id,
            operator=operator,
        )
        session.add(row)
        audit.record(
            audit.CONNECTION_TESTED,
            object_type="ConnectionTest",
            object_id=row.id,
            after={
                "environmentKey": environment_key,
                "result": row.result,
                "version": version,
                "missingScopes": sorted(missing),
                "capabilityProfileArtifactId": artifact_id,
            },
            operator=operator,
            session=session,
        )
        session.flush()

    outcome = ConnectionCheck(
        ok=ok,
        version=version,
        checks=check_documents,
        capability_profile_artifact_id=artifact_id,
        granted_authorities=granted,
        missing_authorities=missing,
        account_roles=roles_by_scope,
        account_org_scope=org_scope_by_scope,
        environment_key=environment_key,
        tested_at=tested_at.isoformat(),
        result="PASSED" if ok else "FAILED",
        dhis2_version=version,
        required_scopes=[item.value for item in wanted],
        message=None if ok else "连接测试未通过，见各检查项",
    )
    idempotency.complete(
        IdemScope.CONNECTION_TEST, idempotency_key, outcome.model_dump(mode="json")
    )
    return outcome


def latest_connection_test(session: Any, environment_key: str) -> ConnectionCheck | None:
    """最近一次连接测试结果（DX-CON-005）：**只读 ``dx``，一个 DHIS2 请求都不发**。

    部署控制台要在不访问实例的前提下显示连接状态（S1 CP-UI-012），所以这里给出的必须是
    已持久化的那份事实，而不是"现在再测一次"的结论。没有记录时返回 ``None``：调用方据此
    显示"从未测试过"，与"测过且失败"是两回事。

    环境不存在时按 DX-ENV-004 报**环境**问题而不是返回 ``None``：把"没登记过这个环境"
    说成"没有连接测试记录"会让调用方去测一个根本不存在的环境。
    """
    require_environment(session, environment_key)
    row = session.execute(
        select(ConnectionTestRow)
        .where(ConnectionTestRow.environment_key == environment_key)
        .order_by(ConnectionTestRow.tested_at.desc())
        .limit(1)
    ).scalars().first()
    if row is None:
        return None
    passed = row.result == "PASSED"
    return ConnectionCheck(
        ok=passed,
        version=row.dhis2_version,
        checks=list(row.checks),
        capability_profile_artifact_id=row.capability_profile_artifact_id,
        granted_authorities=dict(row.granted_authorities or {}),
        missing_authorities=dict(row.missing_authorities or {}),
        environment_key=row.environment_key,
        tested_at=row.tested_at.isoformat(),
        result=row.result,
        dhis2_version=row.dhis2_version,
        required_scopes=list(row.required_scopes or []),
        message=None if passed else "连接测试未通过，见各检查项",
        connection_test_id=row.id,
        operator=row.operator,
    )


def _check_scope(client: Dhis2Client, scope: CredentialScope, account_authorities: list[str]) -> auth.ScopeReport:
    """只读判定一个凭证范围。绝不试写（DX-CON-003、DX-AC-01）。"""
    probe = _READ_PROBES.get(scope)
    read_worked = True
    if probe is not None:
        logical_name, kwargs = probe
        if client.target.profile.resource(logical_name) is None:
            return auth.evaluate(scope, account_authorities, read_worked=True)
        try:
            read_rows = client.call(
                logical_name,
                scope=scope,
                dhis2_type=kwargs.get("dhis2_type"),
                params={"pageSize": "1", "fields": "id"},
            )
        except Dhis2Error as exc:
            read_worked = exc.error_code not in {"DHIS2_PERMISSION_ERROR", "DHIS2_AUTH_ERROR"}
            if not read_worked:
                return auth.ScopeReport(
                    scope=scope,
                    satisfied=False,
                    granted=tuple(sorted(account_authorities)),
                    missing=(f"该范围的只读探测被实例拒绝（HTTP {exc.details.get('status')}）",),
                    superuser=auth.is_superuser(account_authorities),
                    note=f"该范围的只读探测被拒：{exc.error_code}",
                )
    else:
        read_rows = None

    if scope is CredentialScope.IMPORT:
        return _check_import_scope(client, account_authorities, read_rows)
    return auth.evaluate(scope, account_authorities, read_worked=read_worked)


def _first_read_reference(response: Any, profile: Any) -> dict[str, str] | None:
    """刚才那次只读探测里第一个对象的类型与 UID —— 共享判定的取材点。"""
    payload = getattr(response, "payload", None)
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if not isinstance(value, list) or not value:
            continue
        first = value[0]
        if isinstance(first, dict) and first.get("id"):
            return {"dhis2Type": _type_for_collection(str(key), profile), "uid": str(first["id"])}
    return None


def _type_for_collection(collection: str, profile: Any) -> str:
    """集合名 → 类型名，按该版本目录反查（DX-CLI-005：不在代码里写死版本相关路径）。"""
    for dhis2_type in profile.supported_types:
        try:
            if profile.type_resource(dhis2_type).path == collection:
                return str(dhis2_type)
        except Exception:  # noqa: BLE001 - 目录里个别条目缺路径不该让整个判定崩掉
            continue
    return collection.removesuffix("s")


def _check_import_scope(
    client: Dhis2Client, account_authorities: list[str], read_rows: Any
) -> auth.ScopeReport:
    """元数据导入权限的**只读**证据：对象自己的 ``access.manage``。

    DHIS2 没有一个"能导入元数据"的全局授权名——它按对象级共享判定，所以既不能用 ``F_*``
    假判定，也不能"试写一条看看"（那会改变目标环境，违 DX-CON-003）。
    ``GET /api/{type}/{uid}?fields=id,access`` 是只读的，而 ``access.manage`` 就是实例对
    "当前用户能不能管这个对象"的答复。

    这里过去读的是 ``/{uid}/sharing`` 并取 ``payload["manage"]``。那个子资源回的是共享表
    （``owner`` / ``public`` / ``users`` / ``userGroups``），**里面根本没有 manage 这个键**，
    于是 ``payload.get("manage")`` 恒为 False：一个非超级用户的账户在任何真实实例上都会被判
    成不可导入，而连接测试失败会挡住整个 Deployment Run。
    """
    base = auth.evaluate(CredentialScope.IMPORT, account_authorities, read_worked=True)
    found = _first_read_reference(read_rows, client.target.profile)
    if found is None:
        return auth.ScopeReport(
            scope=CredentialScope.IMPORT,
            satisfied=base.satisfied,
            granted=base.granted,
            missing=base.missing,
            superuser=base.superuser,
            note=(
                "实例上还没有可判定的元数据对象，导入权限按账户认证与读可用给出结论；"
                "首次导入前请在真正有对象的环境上重测"
            ),
        )
    uid = str(found["uid"])
    access_resource = client.target.profile.resource("metadata.access")
    if access_resource is None:
        return base
    try:
        payload = client.call(
            "metadata.access",
            scope=CredentialScope.IMPORT,
            dhis2_type=str(found["dhis2Type"]),
            path_args={"uid": uid},
        ).payload
    except Dhis2Error as exc:
        if exc.error_code in {"DHIS2_PERMISSION_ERROR", "DHIS2_AUTH_ERROR"}:
            return auth.ScopeReport(
                scope=CredentialScope.IMPORT,
                satisfied=False,
                granted=base.granted,
                missing=(f"读取对象 {uid} 的访问权被拒（{exc.error_code}）",),
                superuser=base.superuser,
                note="该账户连对象的访问权都读不到，导入几乎肯定会被拒",
            )
        return base
    access = payload.get("access") if isinstance(payload, dict) else None
    if not isinstance(access, dict):
        # 没带回 access 块就是"这一趟没能判定"，不是"判定为不可"。把它读成拒绝，会让一个
        # 字段拼写或一个精简视图把整个环境永久挡在连接测试之外。
        return base
    if access.get("manage") or base.superuser:
        return base
    return auth.ScopeReport(
        scope=CredentialScope.IMPORT,
        satisfied=False,
        granted=base.granted,
        missing=(f"账户对对象 {uid} 没有 manage 权限，元数据导入会被拒",),
        superuser=base.superuser,
        note=auth.SCOPE_NOTES[CredentialScope.IMPORT.value],
    )


#: ``/api/me`` 默认只回一个极简文档，不带 authorities 与 userRoles —— 必须显式要字段，
#  否则“账户没有任何授权”会被当成事实报告出去。实测 2.41：``authorities[:is]`` 返回的是
#  **字符串列表**而不是对象列表；``credentials`` 字段不存在，组织范围在三个 OU 列表里。
_ME_FIELDS = (
    "id,name,username,authorities[:is],userRoles[id,name],"
    "organisationUnits[id,name],dataViewOrganisationUnits[id,name],"
    "teiSearchOrganisationUnits[id,name]"
)


def _names(items: Any) -> list[str]:
    """一个嵌套字段集合的名称：2.41 可能回字符串，也可能回 ``{name, id}``。"""
    found: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            label = item.get("name") or item.get("code") or item.get("id")
            if label:
                found.append(str(label))
    return found


def _account(client: Dhis2Client) -> tuple[list[str] | None, list[str], dict[str, Any]]:
    try:
        me = client.call("me", params={"fields": _ME_FIELDS}).payload
    except Dhis2Error:
        return None, [], {}
    if not isinstance(me, dict):
        return None, [], {}
    authorities = _names(me.get("authorities"))
    roles = _names(me.get("userRoles"))
    org_scope: dict[str, Any] = {}
    for key in ("organisationUnits", "dataViewOrganisationUnits", "teiSearchOrganisationUnits"):
        names = _names(me.get(key))
        if names:
            org_scope[key] = names
    credentials = me.get("credentials")
    if isinstance(credentials, dict):
        org_scope.update(
            {k: v for k, v in credentials.items() if isinstance(v, (str, int, bool))}
        )
    return authorities, roles, org_scope


def _scopes_to_check(required_scopes: Sequence[str] | None) -> list[CredentialScope]:
    """元数据读取与导入总检查；其余按调用方指定（总体 SRS 14.2）。"""
    wanted = list(ALWAYS_CHECKED)
    for item in required_scopes or []:
        try:
            scope = CredentialScope(str(item).lower())
        except ValueError:
            continue
        if scope not in wanted:
            wanted.append(scope)
    return wanted


def _version_ok(version: str, constraint: str | None) -> bool:
    if not constraint:
        return True
    normalized = constraint.strip().lower().replace(".x", "")
    if normalized in {"", "any", "*"}:
        return True
    return version.startswith(normalized)


def _require_environment(session: Any, environment_key: str) -> EnvironmentRow:
    try:
        return require_environment(session, environment_key)
    except EnvironmentInvalidError:
        raise


__all__ = ["ALWAYS_CHECKED", "OPTIONAL_SCOPES", "latest_connection_test", "test_connection"]
