"""供 S2 的只读查询（DX-QRY-001…005）。

S2 不允许访问 DHIS2（只有执行层可以），但它需要四件事：生成 UID、查 UID 是否被占用、
查 code 是否被占用、查对象是否还有关联数据（删除安全）。这些查询全部只用 discovery 凭证
范围，除审计外**不持久化**（DX-QRY-005）：它们是无状态的读，缓存反而会给出过期答案。
"""

from __future__ import annotations

from factory_dhis2.queries.associated_data import AssociatedDataVerdict, has_associated_data
from factory_dhis2.queries.existence import (
    CodeOccupancy,
    UidOccupancy,
    code_exists,
    uid_exists,
)
from factory_dhis2.queries.uids import generate_uids

__all__ = [
    "AssociatedDataVerdict",
    "CodeOccupancy",
    "UidOccupancy",
    "code_exists",
    "generate_uids",
    "has_associated_data",
    "uid_exists",
]
