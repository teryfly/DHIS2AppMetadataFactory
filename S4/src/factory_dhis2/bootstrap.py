"""S4 启动：``initialize()`` 与 ``MIGRATION_CONFIG``（00 文档 6.10）。

登记 S0 拒绝接受未登记项的四类目录：S4 拥有的产物类型、附录 A 的审计事件、S4 的错误码
与 ID 前缀；并装载版本能力 Profile（DX-CAP-001）。S1 在进程启动时**无参**调用本函数
（``factory_control/bootstrap.py``），因此它必须能在没有打开事务时自行开一段短事务。
"""

from __future__ import annotations

from pathlib import Path

from factory_foundation.api import (
    MigrationConfig,
    current_session,
    has_active_uow,
    register_artifact_types,
    register_audit_event_types,
    register_error_codes,
    register_id_prefixes,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_dhis2.registries import (
    DX_ARTIFACT_MAX_SIZES,
    DX_ARTIFACT_TYPES,
    DX_AUDIT_EVENT_TYPES,
    DX_ERROR_CODES,
    DX_ID_PREFIXES,
)

DX_MIGRATION_SCRIPT_LOCATION = str(Path(__file__).resolve().parent / "migrations")

#: 交给 S0 的运行器，它按 ``core → dx → bp → ai → cp`` 排序（00 文档 6.10）。
MIGRATION_CONFIG = MigrationConfig(
    schema="dx",
    script_location=DX_MIGRATION_SCRIPT_LOCATION,
    version_table="dx_alembic_version",
)


def _register_all(session: Session) -> None:
    register_artifact_types(DX_ARTIFACT_TYPES, max_sizes=DX_ARTIFACT_MAX_SIZES, session=session)
    register_audit_event_types(DX_AUDIT_EVENT_TYPES, session=session)
    register_error_codes(DX_ERROR_CODES)
    register_id_prefixes(DX_ID_PREFIXES)
    session.flush()


def initialize(*, session: Session | None = None) -> None:
    """登记 S4 的目录项（FD-ASM-01），并装载版本 Profile。

    ``session`` 让 S1 把这次登记并入自己的事务；不给则开一段短事务提交。装载 Profile
    是纯内存的数据读取，失败即抛错：一个连资源目录都装不起来的 S4 不可能正确访问任何
    实例（DX-CLI-005）。

    ``FACTORY_DX_*`` 覆盖在这一步进入进程：模块级 ``_SETTINGS`` 是 import 期的裸默认，
    以前没有任何启动路径调过 ``configure()``，于是整套 DX 旋钮（超时、重试、批大小、
    异步导入阈值）被静默钉死在代码默认上。放在 ``initialize()``——S1 保证在用到 S4
    之前调它，测试进程的 env 里没有这些变量，重载结果与默认一致。
    """
    from factory_dhis2.config import configure

    configure(None)

    if session is not None or has_active_uow():
        _register_all(session if session is not None else current_session())
    else:
        with unit_of_work() as sess:
            _register_all(sess)

    from factory_dhis2.profiles.registry import load_builtin_profiles

    load_builtin_profiles()


__all__ = ["DX_MIGRATION_SCRIPT_LOCATION", "MIGRATION_CONFIG", "initialize"]
