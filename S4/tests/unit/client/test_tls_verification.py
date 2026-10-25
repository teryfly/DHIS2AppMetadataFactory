"""TLS 校验与调试级正文日志（DX-CLI-004、DX-CLI-006）。

这里不用 ``httpx.MockTransport``：证书校验发生在 TLS 握手层，mock 的 transport 压根不经过
那一层，"证书无效时请求失败"用 mock **测不到**。所以起一个真实的本地 HTTPS 服务，证书用
``openssl`` 现场生成在 tmp 目录里（不进仓库），让 httpx 走真的握手。
"""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from factory_dhis2 import deps
from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.client.tracing import metrics, reset_metrics
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.errors import Dhis2TlsError
from factory_dhis2.profiles import registry
from factory_dhis2.target import EnvironmentTarget

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="需要 openssl 现生成自签名证书"
)

SECRET = "dhp_pat_tlssecretvalue9"


class LocalInstance(NamedTuple):
    """一台只在测试期间存在的 HTTPS 实例（自签名证书，不在任何信任库里）。"""

    base_url: str
    cert_path: Path


def make_target(instance: LocalInstance, *, tls_required: bool) -> EnvironmentTarget:
    registry.load_builtin_profiles()
    refs = {scope.value: f"memory://dx/tls/{scope.value}" for scope in CredentialScope}
    return EnvironmentTarget(
        key="tls",
        name="TLS",
        base_url=instance.base_url,
        environment_class=EnvironmentClass.DEV,
        auth_mode=AuthMode.PAT,
        tls_required=tls_required,
        llm_data_policy=LlmDataPolicy.LOCAL_ONLY,
        timeout_seconds=10,
        status=EnvironmentStatus.ACTIVE,
        profile=registry.profile_for_version("2.41.10"),
        dhis2_version="2.41.10",
        credentials=CredentialProvider(
            refs, auth_mode=AuthMode.PAT, resolver=lambda _ref: SECRET
        ),
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 要求这个名字
        body = json.dumps({"serverVersion": "2.41.10"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return None


@pytest.fixture(scope="module")
def instance(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LocalInstance]:
    root = tmp_path_factory.mktemp("tls")
    cert = root / "server.pem"
    key = root / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield LocalInstance(base_url=f"https://{host}:{port}", cert_path=cert)
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _real_transport() -> Iterator[None]:
    """``None`` = 不装 mock transport，走真网络（只打 127.0.0.1）。"""
    deps.reset_providers()
    deps.set_transport(None)
    reset_metrics()
    yield
    deps.reset_providers()
    reset_metrics()


def test_a_required_tls_environment_fails_on_an_untrusted_certificate(
    instance: LocalInstance,
) -> None:
    """DX-CLI-006：要求 TLS 的环境必须校验证书，证书无效时**请求失败**。

    这一条同时证明 ``verify`` 真的进了 httpx：哪里把它写死成 ``False``（或压根没传），
    这个请求就会"成功"。
    """
    with pytest.raises(Dhis2TlsError) as refused:
        Dhis2Client(make_target(instance, tls_required=True)).call("system.info")
    assert refused.value.code == "DHIS2_TLS_INVALID"


def test_an_untrusted_certificate_is_not_retried(instance: LocalInstance) -> None:
    """证书问题不是瞬时故障：一次都不重试（DX-CLI-003 的重试条件里没有 TLS）。"""
    with pytest.raises(Dhis2TlsError):
        Dhis2Client(make_target(instance, tls_required=True)).call("system.info")
    assert metrics.calls[-1].attempts == 1


def test_an_environment_that_does_not_require_tls_proceeds(
    instance: LocalInstance,
) -> None:
    """``tlsRequired=false`` 是部署方的显式选择：跳过校验，但请求照样做完。"""
    response = Dhis2Client(make_target(instance, tls_required=False)).call("system.info")
    assert response.status == 200
    assert response.payload["serverVersion"] == "2.41.10"


def test_the_injected_tls_adapter_is_the_one_that_decides(instance: LocalInstance) -> None:
    """``deps.set_tls_adapter_factory`` 不是摆设：注入的值**就是** httpx 用的那个。

    这是企业 PKI / mTLS 的接法。这里注入 ``False``（跳过校验），于是同一台自签名服务器在
    ``tls_required=True`` 的环境上也应答了——说明 Client 读的是注入结果，不是档案开关。
    """
    seen: list[str] = []

    def adapter(target: Any) -> Any:
        seen.append(target.key)
        return False

    deps.set_tls_adapter_factory(adapter)
    response = Dhis2Client(make_target(instance, tls_required=True)).call("system.info")
    assert response.status == 200
    assert seen == ["tls"]


def test_a_ca_bundle_makes_the_same_server_trusted(instance: LocalInstance) -> None:
    """正确的解法是把签发方交给校验：CA 里放上这张证书，握手就过了。"""
    from factory_dhis2.client import tls

    context = tls.verification_for(
        instance.base_url, tls_required=True, ca_bundle=str(instance.cert_path)
    )
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    deps.set_tls_adapter_factory(lambda _target: context)
    response = Dhis2Client(make_target(instance, tls_required=True)).call("system.info")
    assert response.status == 200


def test_verification_for_keeps_requiring_certificates_by_default(
    instance: LocalInstance,
) -> None:
    """默认不带 CA bundle 时用系统信任库，且强制校验（这才是"必须校验证书"）。"""
    from factory_dhis2.client import tls

    context = tls.verification_for(instance.base_url, tls_required=True)
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert tls.verification_for(instance.base_url, tls_required=False) is False
    with pytest.raises(Dhis2TlsError):
        tls.verification_for("http://dhis.example.org", tls_required=True)


def test_the_verification_context_is_built_once(instance: LocalInstance) -> None:
    """同一个 CA 配置拿到同一个 :class:`ssl.SSLContext`。

    ``ssl.create_default_context()`` 实测 66 毫秒（装载系统信任库）：每个请求都新建的话，
    "校验证书"这一件事会变成整个 Client 的固定开销。
    """
    from factory_dhis2.client import tls

    first = tls.verification_for(instance.base_url, tls_required=True)
    assert tls.verification_for(instance.base_url, tls_required=True) is first
    with_ca = tls.verification_for(
        instance.base_url, tls_required=True, ca_bundle=str(instance.cert_path)
    )
    assert with_ca is not first, "换了 CA bundle 必须是另一个上下文"
