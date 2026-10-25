"""各凭证范围所需权限的判定（DX-CON-002、总体 SRS FR-SEC-010/011）。

这里要说清一个容易被含混过去的事实：DHIS2 对元数据导入**没有一个全局授权名**可以检查——
它按对象的共享与层权（sharing / data capture scope）判定，能 POST ``/api/metadata`` 的账户
未必能改某个具体 Program。因此本模块不做“看有没有 F_SOMETHING”这种假判定，而是：

* 明确账户是否具备 ``ALL``（超级用户）。系统**默认不要求超级用户**（FR-SEC-010），所以
  缺少它只作为事实报告，不构成失败；
* 按范围列出**可以**据以判断的线索：完全没有任何 authority 的账户几乎肯定是一个尚未配置
  权限的服务账户，此时该范围判为缺少权限（DX-AC-01 的情形）；
* 需要导入、Tracker、DataStore、应用上传的范围，用只读的 ``me`` 与目录里的读端点判断，
  绝不用“试写一下”来判断（DX-CON-003）。

权限名一律用常量而不是散落的字符串，便于按实例实情调整。
"""

from __future__ import annotations

from dataclasses import dataclass

from factory_dhis2.enums import CredentialScope

#: DHIS2 的超级用户授权；有它即可做任何事，但系统不要求它
SUPERUSER_AUTHORITY = "ALL"
#: 用户管理相关（M3 演示用户会用到）
USER_AUTHORITY = "F_USER_ACCOUNTS"

#: 每个范围“至少要有这些线索之一”才算账户配置正常。空列表表示该范围不需要额外全局授权，
#  但账户仍需有元数据的对象级访问权——这由实例本身在执行时保证。
SCOPE_HINTS: dict[CredentialScope, tuple[str, ...]] = {
    CredentialScope.DISCOVERY: (),
    CredentialScope.IMPORT: (),
    CredentialScope.TRACKER: (),
    CredentialScope.DATASTORE: (),
    CredentialScope.APPS: (),
    CredentialScope.MAINTENANCE: (SUPERUSER_AUTHORITY,),
}

#: 报告里对每个范围的说明文字（审批人要能看懂“缺少权限”意味着什么）
SCOPE_NOTES: dict[str, str] = {
    CredentialScope.DISCOVERY.value: "只读发现：需要能读 /api/me 与相关元数据集合",
    CredentialScope.IMPORT.value: "元数据导入：受对象级共享与层权约束，无单一全局授权名",
    CredentialScope.TRACKER.value: "Tracker 数据导入：需要目标 Program 的数据捕获权限",
    CredentialScope.DATASTORE.value: "DataStore 读写：需要命名空间的写共享",
    CredentialScope.APPS.value: "应用上传：需要应用管理权限",
    CredentialScope.MAINTENANCE.value: "维护动作：需要超级用户授权",
}


@dataclass(frozen=True)
class ScopeReport:
    """一个凭证范围的权限结论。"""

    scope: CredentialScope
    satisfied: bool
    granted: tuple[str, ...]
    missing: tuple[str, ...]
    superuser: bool
    note: str

    def as_document(self) -> dict[str, object]:
        return {
            "scope": self.scope.value,
            "satisfied": self.satisfied,
            "superuser": self.superuser,
            "granted": list(self.granted),
            "missing": list(self.missing),
            "note": self.note,
        }


def evaluate(
    scope: CredentialScope,
    authorities: list[str],
    *,
    read_worked: bool = True,
) -> ScopeReport:
    """按范围判定。

    ``read_worked`` 是调用方给出的**事实**：该范围的只读探测是否成功。没有比这更可靠的
    证据了，而且它不改变实例状态。
    """
    granted = tuple(sorted(authorities))
    superuser = SUPERUSER_AUTHORITY in granted
    hints = SCOPE_HINTS.get(scope, ())
    missing: list[str] = [hint for hint in hints if hint not in granted and not superuser]
    if not granted and scope is not CredentialScope.DISCOVERY:
        # 一个完全没有授权的账户：无论什么范围都不可能够用
        missing = missing or ["<no authorities on the service account>"]
    satisfied = read_worked and not missing
    return ScopeReport(
        scope=scope,
        satisfied=satisfied,
        granted=granted,
        missing=tuple(missing),
        superuser=superuser,
        note=SCOPE_NOTES.get(scope.value, ""),
    )


def is_superuser(authorities: list[str]) -> bool:
    return SUPERUSER_AUTHORITY in authorities


__all__ = [
    "SCOPE_HINTS",
    "SCOPE_NOTES",
    "SUPERUSER_AUTHORITY",
    "USER_AUTHORITY",
    "ScopeReport",
    "evaluate",
    "is_superuser",
]
