"""一次执行的上下文装配（00 文档 6.2 的短事务模式）。

执行类函数是外部 I/O：不能在调用方的事务里跑，但又要读 ``dx`` 与产物存储。模式是
**读→做→写** 三段各自开短事务，中间的 DHIS2 调用不持有数据库连接——否则一个 60 秒的
DHIS2 请求会占住一个数据库事务，实例一慢整个 Factory 就跟着卡。

本模块只负责“读”那一段：把环境目标与载荷包一次性装配好，后续步骤不再回数据库取配置。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_foundation.api import unit_of_work

from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.enums import CredentialScope
from factory_dhis2.environment.loader import build_target
from factory_dhis2.payload.package import MetadataPackage, read_package, require_complete
from factory_dhis2.target import EnvironmentTarget


@dataclass
class RunContext:
    """一次执行所需的全部已解析输入。"""

    environment_key: str
    project_id: str | None
    target: EnvironmentTarget
    package: MetadataPackage
    client: Dhis2Client
    import_client: Dhis2Client | None = None

    @property
    def payload_hash(self) -> str:
        return self.package.artifact_hash

    def client_for(self, scope: CredentialScope) -> Dhis2Client:
        """按凭证范围取 Client（DX-BR-03：读用 discovery，导入用 import）。"""
        if scope is self.client.default_scope:
            return self.client
        return self.client.with_scope(scope)


def load_context(
    environment_key: str,
    package_artifact_id: str,
    *,
    project_id: str | None = None,
    scope: CredentialScope = CredentialScope.DISCOVERY,
    require_probe: bool = True,
    complete_manifest: bool = True,
) -> RunContext:
    """读一段：环境目标 + 载荷包。"""
    with unit_of_work() as session:
        target = build_target(session, environment_key, require_probe=require_probe)
    # DX-CLI-005：端点、参数名与分页方式按**该环境探测确认过的那份能力**解析。
    # 读产物是对象存储 I/O，所以放在事务外（00 文档 6.2）。
    target = apply_environment_capability(target)
    package = read_package(package_artifact_id)
    if complete_manifest:
        require_complete(package, "execution")
    if package.project_id and project_id and package.project_id != project_id:
        raise ValueError(
            f"the package belongs to project {package.project_id}, not {project_id}"
        )
    client = Dhis2Client(target, scope=scope)
    return RunContext(
        environment_key=environment_key,
        project_id=project_id or package.project_id,
        target=target,
        package=package,
        client=client,
    )


def document_of(package: MetadataPackage) -> dict[str, Any]:
    """给报告与产物用的载荷摘要（不含内容本体，内容在产物存储里）。"""
    return {
        "artifactId": package.artifact_id,
        "artifactHash": package.artifact_hash,
        "blueprintVersion": package.blueprint_version,
        "environmentKey": package.environment_key,
        "objectCount": package.object_count,
        "types": sorted(str(key) for key in package.payload),
    }


__all__ = ["RunContext", "document_of", "load_context"]
