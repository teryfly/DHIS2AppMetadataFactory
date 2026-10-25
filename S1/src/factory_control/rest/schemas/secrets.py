"""``/api/v1/secrets`` contracts (S5IF-SEC01…03).

The write body is the one place in the API that carries a credential *value*, and the response is
the one place that must prove it kept nothing: :class:`SecretView` has no field a ciphertext, a
mask, a length or a prefix could be returned in (S5IF-G09).  A client that wanted to display
"••••1a2b" would have to build it itself, which is exactly the display this API refuses to enable.

``material`` deliberately carries **no** pydantic constraint.  A constraint failure is rendered by
pydantic with the rejected input in it, so a ``min_length`` on this field would put the secret into
the error body — the emptiness check lives in the service instead, where the message can name the
field without quoting it.  The 422 handler scrubs pydantic's own rendering as a second layer
(:func:`factory_control.rest.app._without_secret_input`); the first layer is that the response
message is the catalogue's sentence, and neither layer is supposed to be the only one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel


class SecretWriteRequest(CamelModel):
    #: The value, once.  Never read back, never logged, never in an audit row.
    material: str


class SecretView(CamelModel):
    name: str
    ref: str
    algo: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: ``None`` means this is the first value ever stored under the name — which is how a client
    #: tells a rotation from a creation without the endpoint having to remember the difference.
    rotated_at: datetime | None = None
    created_by: str = ""


class SecretListView(CamelModel):
    items: list[SecretView] = Field(default_factory=list)
    total: int = 0
    generated_at: datetime | None = None


class SecretDeletedView(CamelModel):
    name: str
    deleted: bool = True
