"""演示用户（M3，本轮**注册但拒绝**，DX-TRK-003、FR-SEC-012）。

演示用户不是一个执行器（它属于 Fixture 导入里的一步，因此没有对应的 ``ExecutorType``），
但它有独立的凭证写入后果：按 Fixture 里的用户规格创建账户、一次性生成密码并经 S0 写入
Secret Manager，执行报告里只出现 Secret 引用。这一整套依赖 S0 的 Secret 写入 + M3 的用户
规格格式，本轮没有，所以照旧拒绝且**不创建任何东西**。

禁止 PRODUCTION 这条规则（DX-TRK-003 末句）在拒绝消息里一并说明：即便到了 M3，实现的第一步
也是环境类别检查，而不是先把账户建出来再解释。
"""

from __future__ import annotations

from typing import Any

from factory_dhis2.executors.base import (
    DEMO_USERS_FEATURE,
    DEMO_USERS_MILESTONE,
    DEMO_USERS_RULE,
    refuse,
)


class DemoUserProvisioner:
    """M3 的演示用户创建：注册存在，任何动作都拒绝。"""

    feature = DEMO_USERS_FEATURE
    milestone = DEMO_USERS_MILESTONE
    rule = DEMO_USERS_RULE

    @property
    def enabled(self) -> bool:
        return False

    def plan(self, fixture_document: dict[str, Any]) -> list[dict[str, Any]]:
        return refuse(
            f"{self.feature} plan",
            self.milestone,
            rule=self.rule,
            fixtureKeys=sorted(fixture_document)[:10],
        )

    def create(
        self, environment_key: str, fixture_document: dict[str, Any], *, execution_id: str
    ) -> list[dict[str, Any]]:
        return refuse(
            f"{self.feature} create",
            self.milestone,
            rule=self.rule,
            environmentKey=environment_key,
            executionId=execution_id,
        )


DEMO_USER_PROVISIONER = DemoUserProvisioner()

__all__ = ["DEMO_USER_PROVISIONER", "DemoUserProvisioner"]
