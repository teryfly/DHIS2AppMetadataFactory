"""认证：按凭证范围即时解析，只在内存（DX-CLI-002、DX-BR-03、FR-SEC-007）。

链路是 ``Secret 引用 → Secret Manager → DHIS2 Client``：本模块拿到的是引用，值来自 S0
的 Secret 适配器，用完就丢。浏览器与其他子系统永远看不到它（FR-SEC-006/007）。
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from factory_foundation.api import SecretUnavailableError, resolve_secret

from factory_dhis2.client import redaction
from factory_dhis2.enums import AuthMode, CredentialScope
from factory_dhis2.errors import Dhis2AuthError

#: 引用解析函数签名，测试可注入（不改动 S0 的全局后端）。
SecretResolver = Callable[[str], str]


def default_resolver(ref: str) -> str:
    return resolve_secret(ref).reveal()


@dataclass(frozen=True)
class Credentials:
    """一个环境在本次请求里用到的凭证，值已解析、只活在内存。"""

    environment_key: str
    scope: CredentialScope
    secret_ref: str
    value: str
    auth_mode: AuthMode

    def header(self) -> dict[str, str]:
        if self.auth_mode is AuthMode.PAT:
            return {"Authorization": f"Bearer {self.value}"}
        # BASIC：Secret 值的形态是 ``user:password``
        user, _, password = self.value.partition(":")
        if not _:
            raise Dhis2AuthError(
                f"basic auth secret for {self.environment_key}/{self.scope.value} "
                "is not in 'user:password' form",
                details={
                    "environmentKey": self.environment_key,
                    "scope": self.scope.value,
                    "secretRef": self.secret_ref,
                },
            )
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def __repr__(self) -> str:  # 永不在表示里露出值
        return (
            f"Credentials(environmentKey={self.environment_key!r}, "
            f"scope={self.scope.value!r}, authMode={self.auth_mode.value!r}, "
            f"secretRef={self.secret_ref!r})"
        )

    def __str__(self) -> str:
        return repr(self)


class CredentialProvider:
    """按 ``(environmentKey, scope)`` 取凭证。"""

    def __init__(
        self,
        refs: Mapping[str, str],
        *,
        auth_mode: AuthMode = AuthMode.PAT,
        resolver: SecretResolver | None = None,
    ) -> None:
        self._refs = {str(scope): str(ref) for scope, ref in refs.items()}
        self._auth_mode = auth_mode
        self._resolver = resolver or default_resolver

    @property
    def configured_scopes(self) -> tuple[CredentialScope, ...]:
        found: list[CredentialScope] = []
        for scope in CredentialScope:
            if scope.value in self._refs:
                found.append(scope)
        return tuple(found)

    def has(self, scope: CredentialScope) -> bool:
        return scope.value in self._refs

    def resolve(self, environment_key: str, scope: CredentialScope) -> Credentials:
        ref = self._refs.get(scope.value)
        if ref is None:
            raise Dhis2AuthError(
                f"environment {environment_key!r} has no credential configured for "
                f"scope {scope.value!r}",
                details={
                    "environmentKey": environment_key,
                    "scope": scope.value,
                    "configured": sorted(self._refs),
                },
            )
        try:
            value = self._resolver(ref)
        except SecretUnavailableError as exc:
            # 带着引用而不是值报错：审计与异常都不许出现凭证明文
            raise Dhis2AuthError(
                f"secret for {environment_key}/{scope.value} cannot be resolved: {exc.message}",
                details={
                    "environmentKey": environment_key,
                    "scope": scope.value,
                    "secretRef": ref,
                },
            ) from exc
        redaction.register_secret_value(value)
        return Credentials(
            environment_key=environment_key,
            scope=scope,
            secret_ref=ref,
            value=value,
            auth_mode=self._auth_mode,
        )


__all__ = ["CredentialProvider", "Credentials", "SecretResolver", "default_resolver"]
