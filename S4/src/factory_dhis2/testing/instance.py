"""内存 DHIS2 实例：2.41 的**响应语义**，不是 Client 的替身（DX-API-001、DX-NFR-05）。

它存在的理由只有一个：让 ``Dhis2Client``、分页、重试、ImportReport 解析、异步任务轮询这些
真实代码路径在测试里被真的走一遍。如果测试把 Client 换成一个返回 Python 对象的 Fake，那些
路径就永远不被执行，而线上出问题恰恰出在它们身上。

因此这里刻意复刻 2.41 的**不直观之处**（都是在线契约测试实测到的事实）：

* ``GET /api/me`` 不带 ``fields`` 时**不返回** ``authorities`` 与 ``userRoles``；带了才返回，
  且 ``authorities`` 是**字符串列表**而不是对象列表；
* 元数据集合返回 ``pager`` + 类型键（``dataElements``），分页参数是 ``page`` / ``pageSize``；
* ``identifier=ASSIGNED`` 的导入对新对象**必须**失败（DHIS2 的 UID 由实例生成）；
* 异步导入立即返回 ``202`` + ``Location`` 指向 ``system/tasks/dataImport/{id}``，任务未跑完时
  该 URL 返回**没有统计键**的文档；
* 未知路径回 HTML 而不是 JSON。

写入行为按 ``importMode`` 区分：VALIDATE 只算结论、不改状态（AC-EXE-01 的那条线在假实例上
同样成立，否则“假实例通过”等于什么都没测）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_VERSION = "2.41.10"
#: 兜底用的类型 → 复数路径表；真正的表从版本 Profile 的 ``typeEndpoints`` 推导（见下）
_FALLBACK_PLURALS: dict[str, str] = {
    "category": "categories",
    "categoryCombo": "categoryCombos",
    "categoryOption": "categoryOptions",
    "categoryOptionCombo": "categoryOptionCombos",
    "constant": "constants",
    "dataElement": "dataElements",
    "dataElementGroup": "dataElementGroups",
    "dataSet": "dataSets",
    "indicator": "indicators",
    "optionSet": "optionSets",
    "organisationUnit": "organisationUnits",
    "organisationUnitGroup": "organisationUnitGroups",
    "organisationUnitGroupSet": "organisationUnitGroupSets",
    "program": "programs",
    "programIndicator": "programIndicators",
    "programRule": "programRules",
    "programStage": "programStages",
    "trackedEntityAttribute": "trackedEntityAttributes",
    "trackedEntityType": "trackedEntityTypes",
    "user": "users",
    "validationRule": "validationRules",
}


def _plural_map() -> dict[str, str]:
    """类型 → 集合路径，直接取自 S4 自己要用的那份 Profile。

    假实例的“有哪些类型”不能由自己另写一张表：那会出现“假实例答应有、真目录没有”或反过来
    的偏差，而这两种偏差正好是探测与发现要测的东西（0.2 权威顺序第 2 条）。
    """
    try:
        from factory_dhis2.profiles import registry

        registry.load_builtin_profiles()
        profile = registry.newest_profile()
    except Exception:  # pragma: no cover - Profile 数据随包发布，缺席只在打包错误时发生
        return dict(_FALLBACK_PLURALS)
    derived = {name: profile.type_resource(name).path for name in profile.supported_types}
    return derived or dict(_FALLBACK_PLURALS)


PLURAL_KEYS: dict[str, str] = _plural_map()
_TASK_URL_RE = re.compile(r"^system/tasks/(?P<category>[A-Za-z_]+)/(?P<id>[^/]+)$")
#: 表达式/条件校验端点（``validationRules/expression/validate`` 等）的形状
_VALIDATE_PATH_RE = re.compile(r"^[A-Za-z]+/(expression|conditions?)/validate$")
#: 2.41 实测：异步元数据导入的任务类别，以及任务日志里那几条进度条目
_ASYNC_CATEGORY = "METADATA_IMPORT"
_TASK_PROGRESS_MESSAGES: tuple[str, ...] = (
    "Loading file resource",
    "Loading file content",
    "Running preCreateBundle",
    "Creating bundle",
    "Running postCreateBundle",
    "Validating bundle",
)


@dataclass
class _Task:
    """一次异步导入：终态可以尚未到达，结论可以由已安装的 ImportReport 推导。"""

    id: str
    category: str = "METADATA_IMPORT"
    report: dict[str, Any] | None = None
    completed: bool = True


@dataclass
class FakeDhis2Instance:
    """一个可脚本化的 2.41 实例。

    ``objects`` 是环境状态（按类型 → UID → 内容）。``scripted`` 是**按路径片段**给的干预：
    每一项 ``{"contains": "...", "status": 503, "times": 2}`` 会在接下来的两次匹配请求上
    返回该状态码，用于测重试、超时与“结果不明”。
    """

    version: str = DEFAULT_VERSION
    build_version: str = "2.41.10"
    authorities: list[str] = field(default_factory=lambda: ["F_DATA_MART.Export"])
    roles: list[dict[str, Any]] = field(default_factory=lambda: [{"id": "role1", "name": "Admin"}])
    org_units: list[dict[str, Any]] = field(
        default_factory=lambda: [
            # DX-DSC-006 要按 level 收窄并带回父节点，所以默认数据必须有层级结构；
            # 只给 {"id","name"} 的话组织层级那条路径永远测不到。
            {"id": "ou1", "code": "OU_ROOT", "name": "Root", "level": 1, "namePath": "/Root"},
            {
                "id": "ou2",
                "code": "OU_DISTRICT",
                "name": "District",
                "level": 2,
                "namePath": "/Root/District",
                "parentNode": {"id": "ou1", "name": "Root"},
            },
            {
                "id": "ou3",
                "code": "OU_CHIEF",
                "name": "Chiefdom",
                "level": 3,
                "namePath": "/Root/District/Chiefdom",
                "parentNode": {"id": "ou2", "name": "District"},
            },
        ]
    )
    objects: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    #: UID → 是否具备 manage（元数据导入权限的只读证据，DX-CON-002/003）
    sharing: dict[str, bool] = field(default_factory=dict)
    generated_uids: list[str] = field(default_factory=list)
    #: 每次导入的结果（``None`` 表示按 ``objects`` 自行判定）
    import_reports: list[dict[str, Any]] = field(default_factory=list)
    #: 预置的异步任务 ID 队列；为空表示按参数决定同步还是异步
    async_job_ids: list[str] = field(default_factory=list)
    tasks: dict[str, _Task] = field(default_factory=dict)
    #: 允许的凭证（用于测 401）
    credentials: set[tuple[str, str]] = field(default_factory=set)
    bearer_tokens: set[str] = field(default_factory=set)
    #: 凭证值 → 该服务账户自己的 authorities / roles / organisationUnits。
    #: DX-CON-002 要求报告**每个凭证范围**实际具备与缺少的权限：PRODUCTION 的 discovery 与
    #: import 本来就是两个账户（DX-ENV-005），不区分凭证的话，连接测试会用只读账户的授权列表
    #: 去回答"这个导入账户有没有权限"。
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    scripted: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    #: 已接受的导入次数（测试用来断言“同一幂等键没有提交两次”）
    imports_accepted: int = 0
    deletes_accepted: int = 0
    uid_sequence: int = 0
    import_sequence: int = 0

    @property
    def task_location_path(self) -> str:
        """异步提交响应里 ``Location`` 的路径部分（transport 会补成绝对 URL）。"""
        return f"system/tasks/{_ASYNC_CATEGORY}"

    def __post_init__(self) -> None:
        # ``org_units`` 同时是 /api/me 的组织范围与 organisationUnits 集合的内容。不播种到
        # ``objects`` 的话，按深度读层级（DX-DSC-006）在内存实例上永远读回空表，那条路径
        # 就从来没被测过。
        for unit in self.org_units:
            uid = str(unit.get("id") or "")
            if uid:
                self.objects.setdefault("organisationUnit", {})[uid] = dict(unit)

    # --- 状态装配（测试用） -------------------------------------------------

    def put(self, dhis2_type: str, uid: str, **body: Any) -> dict[str, Any]:
        """放一个已存在的对象（等价于环境里已经有它）。"""
        record = {"id": uid, **body}
        record.setdefault("code", uid)
        record.setdefault("name", uid)
        record.setdefault("lastUpdated", "2026-01-01T00:00:00.000")
        self.objects.setdefault(dhis2_type, {})[uid] = record
        return record

    def queue_import_report(self, report: dict[str, Any]) -> None:
        self.import_reports.append(report)

    def fail_next(self, contains: str, status: int, *, times: int = 1) -> None:
        self.scripted.append({"contains": contains, "status": status, "times": times})

    def next_task(self, task_id: str, *, completed: bool = True, report: dict[str, Any] | None = None) -> None:
        """下一次异步导入返回这个任务 ID。"""
        self.async_job_ids.append(task_id)
        self.tasks[task_id] = _Task(id=task_id, completed=completed, report=report)

    # --- 请求处理 ----------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        query: dict[str, Any],
        body: Any,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        """(status, headers, payload)。payload 为 str 时按纯文本回。"""
        record: dict[str, Any] = {
            "method": method,
            "path": path,
            "query": dict(query),
            "body": body,
        }
        self.requests.append(record)
        forced = self._take_scripted(path)
        if forced is not None:
            return forced[0], {}, _tomcat_error(forced[0])
        presented = _credential_of(headers or {})
        if self.accounts and path != "system/info" and presented not in self.accounts:
            # 未知凭证：2.41 回 401 而不是空文档，连接测试据此判"该范围的凭证不被接受"
            return 401, {}, _tomcat_error(401)
        if method == "GET":
            return self._get(path, query, presented)
        if method == "POST":
            return self._post(path, query, body)
        if method == "DELETE":
            return self._delete(path, query, body)
        if method == "PUT":
            return self._put(path, query, body)
        return 405, {}, {"status": "ERROR", "message": f"unsupported method {method}"}

    def _take_scripted(self, path: str) -> tuple[int] | None:
        for item in self.scripted:
            if item["times"] <= 0 or item["contains"] not in path:
                continue
            item["times"] -= 1
            return (int(item["status"]),)
        if any(item["times"] <= 0 for item in self.scripted):
            self.scripted = [item for item in self.scripted if item["times"] > 0]
        return None

    # --- GET --------------------------------------------------------------

    def _get(
        self, path: str, query: dict[str, Any], credential: str = ""
    ) -> tuple[int, dict[str, str], Any]:
        if path == "system/info":
            return (
                200,
                {},
                {"version": self.version, "versionType": "RELEASE", "buildTime": "2024-01-01T00:00:00.000"},
            )
        if path == "me":
            return 200, {}, self._me(query.get("fields"), credential)
        if path == "system/id":
            return 200, {}, self._uids(int(query.get("limit") or 1))
        if path == "identifiableObjects":
            return 200, {}, self._identifiable(query)
        if path == "dataValueSets":
            return 200, {}, {"dataValues": []}
        match = _TASK_URL_RE.match(path)
        if match:
            return self._task(match.group("id"))
        if path == "system/tasks":
            return 200, {}, {"systemJobs": [], "pager": _pager(0, 1, 50)}
        if _VALIDATE_PATH_RE.match(path):
            # 表达式校验端点：探测只需要 200，真校验的结论由测试用 scripted 响应给定
            return 200, {}, {"status": "OK", "results": [{"ok": True}]}
        if path.startswith("tracker/"):
            return 200, {}, {"instances": [], "pager": _pager(0, 1, 50)}
        if path == "dataValues":
            return 200, {}, {"dataValues": []}
        if path.startswith("schemas/"):
            name = path.split("/", 1)[1]
            return self._schema(name)
        if path in ("apps", "dataStore"):
            return 200, {}, []
        if path.startswith("dataStore/"):
            segments = path.split("/")
            store = self.objects.setdefault("__dataStore__", {})
            if len(segments) == 2:
                return 200, {}, sorted(k for k in store if "/" not in k or k.split("/")[0] == segments[1])
            key = "/".join(segments[1:])
            if key not in store:
                return 404, {}, _tomcat_error(404)
            return 200, {}, store[key]
        if path.startswith("eventDataValueSets/") or path.startswith("trackedEntityInstances"):
            return 200, {}, {"instances": [], "pager": _pager(0, 1, 50)}
        dhis2_type = _type_for_path(path)
        if dhis2_type is None:
            return 404, {}, _tomcat_error(404)
        collection = self.objects.get(dhis2_type, {})
        if "/" in path:
            head, _, tail = path.partition("/")
            if tail.endswith("/sharing"):
                # 共享表子资源。实测 2.41 回的是 owner/public/users/userGroups —— **没有**
                # manage 这个键。这里曾经回一个顶层 manage 布尔，于是任何按它判定导入权的
                # 代码都能在替身上跑绿、在真实例上永远判否。
                uid = tail[: -len("/sharing")]
                if uid not in collection:
                    return 404, {}, _tomcat_error(404)
                return 200, {}, {
                    "sharing": {
                        "owner": str(collection[uid].get("owner") or "user1"),
                        "public": "rw------" if self.sharing.get(uid, True) else "------",
                        "external": False,
                        "users": {},
                        "userGroups": {},
                    }
                }
            uid = path.split("/", 1)[1]
            if uid not in collection:
                return 404, {}, _tomcat_error(404)
            projected = _project(collection[uid], query.get("fields"))
            if "access" in _wanted_fields(query.get("fields")):
                manage = self.sharing.get(uid, True)
                projected["access"] = {
                    "manage": manage,
                    "write": manage,
                    "read": True,
                    "update": manage,
                    "delete": manage,
                }
            return 200, {}, projected
        return 200, {}, self._paged(dhis2_type, query)

    def _me(self, fields: str | None, credential: str | None = None) -> Any:
        """不带 ``fields`` 时 2.41 只回一个极简文档：这是实测行为，必须复刻。

        给了 :attr:`FakeDhis2Instance.accounts` 时，授权与角色按**出示的那份凭证**回：
        DX-CON-002 要的是每个凭证范围自己的账户事实，而不是一个账户的事实被复制四份。
        """
        account = self.accounts.get(credential or "", {}) if self.accounts else {}
        document: dict[str, Any] = {
            "id": str(account.get("id") or "user1"),
            "username": str(account.get("username") or "admin"),
            "displayName": str(account.get("displayName") or "Admin"),
        }
        if not fields:
            return document
        wanted = {item.split("[")[0].strip() for item in fields.split(",")}
        if "authorities" in wanted:
            document["authorities"] = list(account.get("authorities", self.authorities))
        if "userRoles" in wanted:
            document["userRoles"] = list(account.get("roles", self.roles))
        for key in ("organisationUnits", "dataViewOrganisationUnits", "teiSearchOrganisationUnits"):
            if key in wanted:
                document[key] = list(self.org_units)
        if "credentials" in wanted:
            document["credentials"] = {"userOrgUnitHierarchyRoot": "ou1"}
        return document

    def _uids(self, count: int) -> list[str]:
        if self.generated_uids:
            taken = self.generated_uids[:count]
            self.generated_uids = self.generated_uids[count:]
            return taken
        made: list[str] = []
        for _ in range(max(count, 0)):
            self.uid_sequence += 1
            made.append(f"fake{self.uid_sequence:05d}Uidx")
        return made

    def _identifiable(self, query: dict[str, Any]) -> dict[str, Any]:
        """实测：该端点对**存在**的 UID 也可能回空集合，所以 S4 不信它。"""
        return {"identifiableObjects": [], "pager": _pager(0, 1, 50)}

    def _paged(self, dhis2_type: str, query: dict[str, Any]) -> dict[str, Any]:
        plural = PLURAL_KEYS.get(dhis2_type) or f"{dhis2_type}s"
        rows = sorted(self.objects.get(dhis2_type, {}).values(), key=lambda item: str(item.get("id")))
        rows = [row for row in rows if _matches_filters(row, query.get("filter"))]
        page_size = max(int(query.get("pageSize") or 50), 1)
        page = max(int(query.get("page") or 1), 1)
        start = (page - 1) * page_size
        window = rows[start : start + page_size]
        return {
            plural: [_project(row, query.get("fields")) for row in window],
            "pager": _pager(len(rows), page, page_size),
        }

    def _task(self, task_id: str) -> tuple[int, dict[str, str], Any]:
        task = self.tasks.get(task_id)
        if task is None:
            return 404, {}, _tomcat_error(404)
        # 2.41 实测：任务查询返回的是**日志条目数组**，最新的在最前面；没有 ImportReport
        entries: list[dict[str, Any]] = [
            {
                "uid": f"log{index}",
                "id": f"log{index}",
                "level": "INFO",
                "category": task.category,
                "time": f"2026-01-01T00:00:0{index}.000",
                "message": message,
                "completed": False,
            }
            for index, message in enumerate(_TASK_PROGRESS_MESSAGES, start=1)
        ]
        entries.append(
            {
                "uid": "log0",
                "id": "log0",
                "level": "INFO",
                "category": task.category,
                "time": "2026-01-01T00:00:00.000",
                "message": "Metadata import started",
                "completed": False,
                "dataType": "PARAMETERS",
                "data": {"importMode": "COMMIT", "async": True},
            }
        )
        if task.completed:
            terminal = _terminal_log_entry(task)
            entries.insert(0, terminal)
        return 200, {}, entries

    def _schema(self, name: str) -> tuple[int, dict[str, str], Any]:
        if name not in PLURAL_KEYS:
            return 404, {}, _tomcat_error(404)
        return (
            200,
            {},
            {
                "singular": name,
                "plural": PLURAL_KEYS[name],
                "properties": [
                    {"name": key, "required": key in ("name",), "unique": key == "code", "propertyType": "COMMON"}
                    for key in ("id", "code", "name", "lastUpdated", "created", "href")
                ],
            },
        )

    # --- POST / PUT / DELETE ----------------------------------------------

    def _post(self, path: str, query: dict[str, Any], body: Any) -> tuple[int, dict[str, str], Any]:
        if path == "metadata":
            return self._import(path, query, body)
        if path.startswith("metadata/"):
            return self._import(path, query, body)
        if path == "apps":
            return 200, {}, {"key": "web-app", "version": "1.0.0"}
        if path.startswith("dataStore/"):
            namespace = path.split("/")[1] if len(path.split("/")) > 1 else "default"
            self.objects.setdefault("__dataStore__", {})[namespace] = body
            return 200, {}, {"httpStatusCode": "201", "status": "OK"}
        return 404, {}, _tomcat_error(404)

    def _put(self, path: str, query: dict[str, Any], body: Any) -> tuple[int, dict[str, str], Any]:
        if path.startswith("dataStore/"):
            self.objects.setdefault("__dataStore__", {})[path.split("dataStore/", 1)[1]] = body
            return 200, {}, {"status": "OK"}
        if path == "metadata":
            return self._import(path, query, body)
        return 404, {}, _tomcat_error(404)

    def _delete(self, path: str, query: dict[str, Any], body: Any) -> tuple[int, dict[str, str], Any]:
        if path == "metadata":
            self.deletes_accepted += 1
            return self._import(path, query, body)
        dhis2_type = _type_for_path(path)
        if dhis2_type and "/" in path:
            collection = self.objects.get(dhis2_type, {})
            uid = path.split("/", 1)[1]
            if uid not in collection:
                return 404, {}, _tomcat_error(404)
            del collection[uid]
            return 200, {}, {"status": "OK"}
        return 404, {}, _tomcat_error(404)

    def _import(self, path: str, query: dict[str, Any], body: Any) -> tuple[int, dict[str, str], Any]:
        self.imports_accepted += 1
        mode = str(query.get("importMode") or "COMMIT").upper()
        report = self._report_for(mode, query, body)
        if str(query.get("async") or "").lower() in {"true", "1"}:
            task_id = self.async_job_ids.pop(0) if self.async_job_ids else f"job{len(self.tasks) + 1}"
            self.tasks.setdefault(task_id, _Task(id=task_id, report=report, completed=False))
            self.import_sequence += 1
            # 2.41 实测：200 + Location 指向任务“类型”集合，任务 ID 只在响应体里
            return (
                200,
                {"Location": f"/{self.task_location_path}"},
                {
                    "httpStatus": "OK",
                    "httpStatusCode": 200,
                    "status": "OK",
                    "message": f"Initiated {_ASYNC_CATEGORY} ({self.import_sequence})",
                    "response": {
                        "id": task_id,
                        "name": f"{_ASYNC_CATEGORY} ({self.import_sequence})",
                        "created": "2026-01-01T00:00:00.000",
                        "jobType": _ASYNC_CATEGORY,
                        "jobStatus": "RUNNING",
                        "jobParameters": dict(query),
                    },
                },
            )
        inner = report if "response" in report else _envelope(report)
        return int(inner.get("httpStatusCode") or 200), {}, inner

    def _report_for(self, mode: str, query: dict[str, Any], body: Any) -> dict[str, Any]:
        if self.import_reports:
            return self.import_reports.pop(0)
        if isinstance(body, dict):
            return _report_from_payload(body, mode=mode, query=query, instance=self)
        return _running_report()


def _type_for_path(path: str) -> str | None:
    """路径段 → DHIS2 类型；复数形式与 Profile 的 typeEndpoints 一致。"""
    head = path.split("/", 1)[0]
    for dhis2_type, plural in PLURAL_KEYS.items():
        if head in (plural, dhis2_type):
            return dhis2_type
    return None


def _pager(total: int, page: int, page_size: int) -> dict[str, Any]:
    """2.41 实测的 pager 形状：``pageCount`` 是页数，没有 ``pages``/``isLast``。

    刻意不给 ``nextPage``：S4 按 ``page``/``pageCount`` 自己翻页，fake 提供绝对 URL 只会
    诱导出“跟着实例给的 URL 走”这种没有实现过的行为。
    """
    page_count = (total + page_size - 1) // page_size if total else 0
    return {"page": page, "pageSize": page_size, "total": total, "pageCount": page_count}


def _wanted_fields(fields: str | None) -> set[str]:
    """``fields`` 里点名的字段集合，与 :func:`_project` 的切法一致。"""
    if not fields:
        return set()
    return {item.split("[")[0].strip() for item in fields.split(",") if item.strip()}


def _project(row: dict[str, Any], fields: str | None) -> dict[str, Any]:
    """按 ``fields`` 裁剪；``:all`` 与缺省都给全部（2.41 对 ``fields`` 的宽容行为）。

    ``:all`` 是 DHIS2 的真实记号（Snapshot 与部署后验证按 ``id,code,name,created,lastUpdated,
    :all`` 读回）：假实例必须把它**展开成全部字段**，否则读回来的对象缺字段，回读比对就会
    报出“环境里没有这个值”的假不一致——这类假阳性正是内存实例最容易写错的地方。
    """
    if not fields:
        return dict(row)
    wanted = {item.split("[")[0].strip() for item in fields.split(",") if item.strip()}
    if ":all" in wanted or fields == ":all":
        return dict(row)
    out: dict[str, Any] = {key: value for key, value in row.items() if key in wanted}
    if "id" in wanted or not out:
        out.setdefault("id", row.get("id"))
    return out


def _matches_filters(row: dict[str, Any], raw: Any) -> bool:
    """支持 ``field:eq:value`` / ``field:ilike:value`` / ``field:in:[a,b]`` 与数值比较。

    ``le/ge/lt/gt`` 是组织层级按深度收窄用的（``level:le:2``，DX-DSC-006）：不实现它们，
    那条过滤器在内存实例上会被静默忽略，"按深度读层级"就变成"读全部"也照样通过测试。
    """
    filters: list[str] = []
    if isinstance(raw, str):
        filters = [raw]
    elif isinstance(raw, list):
        filters = [str(item) for item in raw]
    for item in filters:
        parts = item.split(":", 2)
        if len(parts) != 3:
            continue
        field_name, operator, expected = parts
        actual = row.get(field_name)
        if operator == "eq" and str(actual) != expected:
            return False
        if operator == "neq" and str(actual) == expected:
            return False
        if operator == "like" and expected.lower() not in str(actual).lower():
            return False
        if operator == "ilike" and expected.lower() not in str(actual).lower():
            return False
        if operator == "in":
            wanted = [value.strip() for value in expected.strip("[]").split(",")]
            if str(actual) not in wanted:
                return False
        if operator in {"le", "lt", "ge", "gt"} and not _compares(actual, operator, expected):
            return False
    return True


def _compares(actual: Any, operator: str, expected: str) -> bool:
    try:
        left = float(str(actual))
        right = float(expected)
    except (TypeError, ValueError):
        # 比较不了的值不该被说成"匹配"：那会让一个错拼的过滤器读回全量数据
        return False
    if operator == "le":
        return left <= right
    if operator == "lt":
        return left < right
    if operator == "ge":
        return left >= right
    return left > right


def _credential_of(headers: dict[str, str]) -> str:
    """把请求头里的凭证还原成 ``accounts`` 的键。

    BASIC 的 Secret 值本身就是 ``user:password``，这里解码回那个形态；PAT 用令牌原文。
    返回值只用于**在内存实例里查账户**，绝不写进请求记录（DX-CLI-004 的脱敏同样适用于替身）。
    """
    import base64

    for name, value in headers.items():
        if name.lower() != "authorization":
            continue
        scheme, _, rest = value.strip().partition(" ")
        if scheme.lower() == "basic":
            try:
                return base64.b64decode(rest.encode()).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return ""
        return rest.strip()
    return ""


def _tomcat_error(status: int) -> str:
    """未知路径上实例回的是 HTML，不是 JSON —— S4 必须能扛住这个。"""
    return (
        f"<html><head><title>Exception Processing {status}</title></head>"
        f"<body><h1>HTTP Status {status}</h1></body></html>"
    )


def _terminal_log_entry(task: _Task) -> dict[str, Any]:
    """那条 ``completed=true`` 的终态日志：结论与计数都在消息文本里（2.41 实测）。"""
    report: dict[str, Any] = dict(task.report or {})
    raw_stats = report.get("stats")
    stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
    status = str(report.get("status") or "OK").upper()

    def count(key: str, *fallbacks: str) -> int:
        for source in (stats, report):
            for name in (key, *fallbacks):
                value = source.get(name)
                if isinstance(value, int):
                    return value
        return 0

    created = count("created", "imported")
    updated = count("updated")
    deleted = count("deleted")
    ignored = count("ignored")
    ok = status in {"OK", "SUCCESS"}
    return {
        "uid": f"terminal-{task.id}",
        "id": f"terminal-{task.id}",
        "level": "INFO" if ok else "ERROR",
        "category": task.category,
        "time": "2026-01-01T00:00:09.000",
        "message": (
            f"Import complete with status {'OK' if ok else 'ERROR'}, {created} created, "
            f"{updated} updated, {deleted} deleted, {ignored} ignored"
        ),
        "completed": True,
    }


def _running_report() -> dict[str, Any]:
    return _envelope(
        {
            "responseType": "ImportReport",
            "status": "OK",
            "stats": {"created": 0, "updated": 0, "deleted": 0, "ignored": 0, "total": 0},
            "typeReports": [],
        }
    )


def _envelope(inner: dict[str, Any]) -> dict[str, Any]:
    """2.41.10 实测的同步导入响应外层：结论在 ``response`` 里，HTTP 状态另算。

    顶层 ``status`` 是 ``OK``/``WARNING``（不是 ``SUCCESS``），真正的判定在
    ``response.status``；校验不通过时实例回 **HTTP 409** 而报告照样完整——S4 必须把报告
    读出来映射成问题清单，而不是把 409 当一个异常丢掉（``client/session.py`` 的同名注释）。
    """
    status = str(inner.get("status") or "OK").upper()
    ok = status in {"OK", "SUCCESS"}
    return {
        "httpStatus": "OK" if ok else "Conflict",
        "httpStatusCode": 200 if ok else 409,
        "status": "OK" if ok else "WARNING",
        "message": (
            "Imported successfully" if ok else "One or more errors occurred, please see full details in import report."
        ),
        "response": inner,
    }


def _java_klass(dhis2_type: str) -> str:
    """``dataElement`` → ``org.hisp.dhis.dataelement.DataElement``。

    包名部分在真实例上并不总是类型的简单小写（``categoryOptionCombo`` 住在 ``category`` 包），
    所以这里只求形状相似：S4 一律按 UID 映射回 businessKey，绝不依赖 klass 的拼写。
    """
    class_name = f"{dhis2_type[0].upper()}{dhis2_type[1:]}"
    return f"org.hisp.dhis.{dhis2_type.lower()}.{class_name}"


def _stored_row(dhis2_type: str, row: dict[str, Any], uid: str) -> dict[str, Any]:
    """实例存对象时**不保存等于默认值的字段**：回读也就没有这个键（2.41.10 实测）。

    假实例必须同样省略，否则“部署后验证如何处理实例省略默认值”这条真实路径在本地永远不会
    被执行到，只能等到在线契约测试才暴露。
    """
    stored = dict(row, id=uid)
    defaults = _property_defaults().get(dhis2_type) or {}
    for key, default in defaults.items():
        if key in stored and str(stored[key]) == str(default):
            del stored[key]
    return stored


def _property_defaults() -> dict[str, dict[str, Any]]:
    try:
        from factory_dhis2.profiles import registry

        registry.load_builtin_profiles()
        return registry.newest_profile().property_defaults
    except Exception:  # pragma: no cover - Profile 随包发布
        return {}


def _report_from_payload(
    payload: dict[str, Any], *, mode: str, query: dict[str, Any], instance: FakeDhis2Instance
) -> dict[str, Any]:
    """按载荷与当前状态算一份 ImportReport（2.41 形状）。

    ``identifier=ASSIGNED`` 带上一个**尚不存在**的 UID 是被接受的：S2 的 UID 本来就来自
    实例的 ``system/id``，实测 2.41.10 对这种 CREATE 回 ``status=OK, created=1``。假实例如果
    反过来拒绝它，本地就会把一条正常路径测成错误路径。
    """
    type_reports: list[dict[str, Any]] = []
    created = updated = ignored = deleted = 0
    strategy = str(query.get("importStrategy") or "CREATE_AND_UPDATE").upper()
    for dhis2_type, rows in _payload_rows(payload):
        collection = instance.objects.setdefault(dhis2_type, {})
        object_reports: list[dict[str, Any]] = []
        type_created = type_updated = type_ignored = 0
        for row in rows:
            uid = str(row.get("id") or row.get("uid") or "")
            if not uid:
                type_ignored += 1
                object_reports.append(_rejected(dhis2_type, row, "No identifier provided", "E1096"))
                continue
            if uid not in collection:
                if strategy in {"CREATE", "CREATE_AND_UPDATE"} and mode != "VALIDATE":
                    collection[uid] = _stored_row(dhis2_type, row, uid)
                type_created += 1
            else:
                if strategy in {"UPDATE", "CREATE_AND_UPDATE"}:
                    if mode != "VALIDATE":
                        collection[uid].update(row)
                    type_updated += 1
                else:
                    type_ignored += 1
                    object_reports.append(
                        _rejected(dhis2_type, row, f"Already exists: {uid}", "E1134")
                    )
        created += type_created
        updated += type_updated
        ignored += type_ignored
        if object_reports:
            type_reports.append(
                {
                    "klass": _java_klass(dhis2_type),
                    "stats": {
                        "created": type_created,
                        "updated": type_updated,
                        "deleted": 0,
                        "ignored": type_ignored,
                        "total": type_created + type_updated + type_ignored,
                    },
                    "objectReports": object_reports,
                }
            )
    failed = bool(type_reports) and created + updated == 0
    inner: dict[str, Any] = {
        "responseType": "ImportReport",
        "status": "ERROR" if failed else "OK",
        "stats": {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "ignored": ignored,
            "total": created + updated + ignored + deleted,
        },
        "typeReports": type_reports,
    }
    if mode == "VALIDATE":
        inner["mode"] = "VALIDATE"
    return inner


def _rejected(dhis2_type: str, row: dict[str, Any], message: str, code: str) -> dict[str, Any]:
    """一条 ``objectReports`` 条目：形态按实测（uid / displayName / errorReports[]）。"""
    uid = str(row.get("id") or row.get("uid") or "")
    name = str(row.get("name") or uid)
    return {
        "klass": _java_klass(dhis2_type),
        "index": 0,
        "uid": uid,
        "displayName": name,
        "errorReports": [
            {
                "message": message,
                "mainKlass": _java_klass(dhis2_type),
                "mainId": uid,
                "errorProperty": "id",
                "errorCode": code,
                "args": [name, uid],
            }
        ],
    }


def _payload_rows(payload: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """载荷里的 (类型, 行) —— 按复数键反查类型，顺序稳定。"""
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for key in sorted(payload):
        value = payload[key]
        if not isinstance(value, list) or not value:
            continue
        dhis2_type = next((name for name, plural in PLURAL_KEYS.items() if key == plural), None)
        if dhis2_type is None:
            continue
        rows = [item for item in value if isinstance(item, dict)]
        if rows:
            out.append((dhis2_type, rows))
    return out


__all__ = ["DEFAULT_VERSION", "FakeDhis2Instance"]
