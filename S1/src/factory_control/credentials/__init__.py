"""Operator-supplied credentials, encrypted at rest (CP-SEC-001…003).

Three pieces, deliberately separate:

* :mod:`.crypto` — HKDF + AES-256-GCM over a deployment master key.
* :mod:`.store` — the ``cp.secrets`` rows themselves.
* :mod:`.backend` — the ``db://`` resolver S3 and S4 see when a reference has to become a header.

The HTTP rules (who may write, what a name may look like, what happens when a credential is still
referenced) live in :mod:`factory_control.services.secrets`, because they are about requests rather
than about this table.

Nothing is re-exported here on purpose: `models` needs the algorithm name from `.crypto`, and `.store`
needs `models`, so an eager import in this `__init__` closes that loop into a cycle. Callers import
the module they mean — `from factory_control.credentials import crypto` — which is also the more
honest reading of a package whose three parts have different jobs.
"""

from __future__ import annotations

__all__: list[str] = []
