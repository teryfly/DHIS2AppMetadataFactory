"""在线契约测试（DX-NFR-05、DX-CAP-007、总体 SRS 25.4）。

用一个真实可达的 DHIS2 实例（清单在 ``live_instances.json``，FR-TST-001）**验证假设是否成立**：
内存假实例是我们自己写的，它不可能告诉我们"这个端点其实不存在"。

默认跳过。开启方式（凭证只经环境变量进入，配置里不写值）：

```bash
export FACTORY_DX_LIVE_DHIS2_VERSION=2.41        # 或 _BASE_URL 直接给地址
export "$(jq -r .credentials.usernameEnv tests/contract/live_instances.json)"=admin
export "$(jq -r .credentials.passwordEnv tests/contract/live_instances.json)"=<官方演示口令>
.venv/bin/pytest -q -m contract tests/contract
```

写入类断言（COMMIT）额外需要 ``FACTORY_DX_LIVE_ALLOW_COMMITS=1``：目标是**公开共享**实例，
任何人都能用同一份凭证，未经许可的写入会影响别人。VALIDATE 与全部读操作不改变环境
（AC-EXE-01），因此不需要这个开关。

25.4 的五条硬约束由 :mod:`tests.contract.live_run` 执行，这个夹具层只负责把它们接上：
① 写入对象的 code 带运行标签（``live_run.code_for``）；
② 引用的既有演示对象在运行前后各取一次内容指纹（``demo_baseline``）；
③ 会话结束删除本次创建的对象，并可清掉早先运行留下的 ``FT_`` 残留（``created`` 的 teardown，
   清理失败只告警）；
④ 只使用合成数据；
⑤ 同一实例上跨进程串行（``instance_lock``）。
FR-TST-003 的"无法判定"由 :func:`live_run.run_with_retries` 给出，体现在 ``live_attempt`` 里。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from factory_foundation.api import configure_object_store
from factory_foundation.testing import InMemoryObjectStore
from tests.contract import live_run

pytestmark = pytest.mark.contract

ENVIRONMENT_KEY = f"contract{live_run.RUN_TAG[:6]}"


def live_base_url() -> str:
    return live_run.live_base_url()


def live_credentials() -> tuple[str, str]:
    """Read the official demo account from the environment (FR-TST-001).

    There is no default and no fallback here on purpose.  A credential baked into the test
    tree is one that nobody rotates, and a value printed into a failure message on a shared
    runner is a leaked value; the configuration file names the variables, this reads them.
    """
    credentials = live_run.catalogue()["credentials"]
    username = os.environ.get(str(credentials["usernameEnv"]))
    password = os.environ.get(str(credentials["passwordEnv"]))
    if not username or not password:
        raise AssertionError(
            f"契约测试需要 {credentials['usernameEnv']} / {credentials['passwordEnv']}；"
            "值只经 S0 Secret 适配器进入内存，不写入任何持久化位置，也不出现在日志与断言里（DX-CLI-002）"
        )
    return str(username), str(password)


def commits_allowed() -> bool:
    return os.environ.get("FACTORY_DX_LIVE_ALLOW_COMMITS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


# re-exported under the names the test module already imports, so the old call sites keep
# working while pointing at the governed implementation
unique_code = live_run.code_for
idem = lambda name: f"contract:{name}:{live_run.RUN_TAG}"  # noqa: E731


def require_live_instance() -> None:
    if not (
        os.environ.get("FACTORY_DX_LIVE_DHIS2_BASE_URL")
        or os.environ.get("FACTORY_DX_LIVE_DHIS2_VERSION")
        or os.environ.get("FACTORY_DX_LIVE_ENABLED")
    ):
        pytest.skip("未配置 FACTORY_DX_LIVE_DHIS2_VERSION / _BASE_URL / _ENABLED，跳过在线契约测试")


@pytest.fixture(scope="module")
def live_enabled() -> None:
    require_live_instance()


@pytest.fixture(scope="module")
def instance_lock(live_enabled: None) -> Iterator[live_run.InstanceLock]:
    """⑤ one regression at a time per instance, across processes.

    Held for the whole module rather than per test: two runs interleaving on a shared public
    instance produce a failure that belongs to neither of them, and the daily schedule would
    otherwise be reporting the other job's activity as our own breakage.
    """
    lock = live_run.InstanceLock(live_base_url(), wait_seconds=0.0)
    try:
        lock.acquire()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    try:
        yield lock
    finally:
        lock.release()


@pytest.fixture(scope="module")
def live_credentials_holder(live_enabled: None) -> tuple[str, str]:
    return live_credentials()


@pytest.fixture(scope="module")
def created(live_enabled: None) -> Iterator[live_run.Created]:
    """③ what this run wrote, deleted afterwards; a cleanup failure only warns.

    The sweep runs before the object store is thrown away, because after that the uids exist
    only in a database the next module reset drops -- and an object left on a public instance
    is left there for good.
    """
    registry = live_run.Created()
    yield registry
    try:
        outcome = registry.sweep(live_base_url(), live_credentials())
    except Exception as exc:  # noqa: BLE001 -- a cleanup problem must not flip a verdict
        print(f"CONTRACT-CLEANUP-WARN {type(exc).__name__}: {exc}")
        return
    for note in outcome.get("warnings") or []:
        print(f"CONTRACT-CLEANUP-WARN {note}")
    residue = registry.sweep_residue(live_base_url(), live_credentials())
    if residue.get("removed"):
        print(f"CONTRACT-CLEANUP swept {residue['removed']} leftover FT_ objects from earlier runs")


@pytest.fixture(scope="module")
def demo_baseline(
    live_enabled: None, live_credentials_holder: tuple[str, str]
) -> Iterator[live_run.DemoBaseline]:
    """② the pre-existing demo objects a run references must come back as they were.

    Refusing to modify them is a rule; showing that nothing changed is a measurement.  A
    Brownfield scenario that needs "an object that already exists" creates it as this run's
    own precondition (25.4 ②), so the only objects read here are ones we never write.
    """
    baseline = live_run.DemoBaseline()
    yield baseline
    base_url, credentials = live_base_url(), live_credentials_holder
    changed = baseline.differences(base_url, credentials)
    assert not changed, (
        f"DX-AC-15: pre-existing demo objects were altered by this run: {changed}"
    )


@pytest.fixture
def live_attempt() -> Iterator[type[live_run]]:
    """FR-TST-003: run something that may be indeterminate, and say which it was."""
    yield live_run


@pytest.fixture(scope="module")
def dx_live_db(live_enabled: None, instance_lock: live_run.InstanceLock) -> None:
    """在线测试也要有 ``dx``：环境档案与产物索引存在数据库里。"""
    from factory_dhis2.testing.harness import Dhis2Harness, resolve_test_database_url

    _ = instance_lock
    harness = Dhis2Harness(url=resolve_test_database_url())
    harness.setup()
    configure_object_store(InMemoryObjectStore())


_PROBED = False


@pytest.fixture
def live_environment(
    dx_live_db: None, created: live_run.Created
) -> Iterator[str]:
    """登记一个指向在线实例的环境，凭证放 S0 的内存 Secret 后端。

    模块内只登记一次并**先探测一次**（``_PROBED`` 记着做过）：发现与执行都以已持久化的
    Capability Profile 为前提（DX-CAP-003），而文件里的任何一条都应该能单独跑起来。
    探测放在函数级夹具里做，因为模块级夹具里开的幂等记录会被后面的用例事务打乱
    ——契约测试要证明的是实例行为，不是夹具装配顺序。

    ``created`` is requested so a module's writes are always registered against something,
    even when a test only reads: a test that starts writing without asking for the fixture
    leaves residue nobody owns.
    """
    global _PROBED
    from factory_dhis2.api import (
        create_environment,
        get_environment,
        probe_capabilities,
        test_connection,
    )

    base_url = live_base_url()
    username, password = live_credentials()
    pinned = next(
        (
            str(item["version"])
            for item in live_run.catalogue()["instances"]
            if str(item["baseUrl"]).rstrip("/") == base_url.rstrip("/")
        ),
        "",
    )
    # "2.41.10" -> "2.41.x"; an instance outside the catalogue has no constraint to claim,
    # and inventing one from the URL tail would let a typo in the config pin nothing
    constraint = ".".join(pinned.split(".")[:2]) + ".x" if pinned.count(".") >= 2 else ""
    from factory_foundation.api import install_development_backends, store_secret

    install_development_backends()
    discovery_ref = store_secret("memory://dx/contract/discovery", f"{username}:{password}")
    import_ref = store_secret("memory://dx/contract/import", f"{username}:{password}")

    _ = created
    if get_environment(ENVIRONMENT_KEY) is None:
        create_environment(
            {
                "environmentKey": ENVIRONMENT_KEY,
                "name": f"DHIS2 契约测试实例（run {live_run.RUN_TAG}）",
                "baseUrl": base_url,
                "environmentClass": "TEST",
                "versionConstraint": constraint,
                "authMode": "BASIC",
                "tlsRequired": True,
                "llmDataPolicy": "PRIVATE_NETWORK",
                "timeoutSeconds": 120,
                "credentials": [
                    {"scope": "discovery", "secretRef": discovery_ref},
                    {"scope": "import", "secretRef": import_ref},
                ],
            }
        )
        _PROBED = False
    if not _PROBED:
        test_connection(ENVIRONMENT_KEY, ["discovery", "import"], idempotency_key=idem("fixture"))
        # The connection test proves the instance answers; only probe_capabilities persists the
        # Capability Profile that every later call refuses to work without (DX-CAP-003).
        # Asking for it here rather than leaving it to whichever test happens to run first is
        # what makes a single `-k` selection of this file behave like the whole module.
        probe_capabilities(ENVIRONMENT_KEY, idempotency_key=idem("fixture:cap"))
        _PROBED = True
    yield ENVIRONMENT_KEY
