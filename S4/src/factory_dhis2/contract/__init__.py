"""S4 对外发布的产物内容格式（DX-CAP-008、DX-DSC-009）。

00 文档 6.1 要求每个库子系统把**内容格式**作为契约发布：S2 编译与 S3 上下文构造都解析
这两个产物，S4 又不允许调用它们，所以格式必须由 S4 单方面定下来并保持稳定。
字段名以 camelCase 为准（与 S2 侧的读取契约一致）：调用方对字段名漂移的容忍度是
``extra="allow"``，也就是说**改名等于悄悄断掉**。

本包只做文档构造与校验，不碰数据库也不碰网络。
"""

from __future__ import annotations

from factory_dhis2.contract.capability_profile import (
    CapabilityProfileDocument,
    DiscoveredSchema,
    ResourceEntryDocument,
    build_capability_profile_document,
)
from factory_dhis2.contract.discovery_result import (
    DiscoveredObjectDocument,
    DiscoveryResultDocument,
    build_discovery_result_document,
)

__all__ = [
    "CapabilityProfileDocument",
    "DiscoveredObjectDocument",
    "DiscoveredSchema",
    "DiscoveryResultDocument",
    "ResourceEntryDocument",
    "build_capability_profile_document",
    "build_discovery_result_document",
]
