"""cp credential store (CP-SEC-001, FR-SEC-006)

Adds ``cp.secrets``: one row per operator-supplied credential, holding only an authenticated
ciphertext, the algorithm that produced it and a fingerprint of the key that opens it.  No column
here contains a password, a token or anything derivable from one — the point of the table is that
the *business database* stops being the place a DHIS2 token lives in plaintext.

Purely additive: no existing table is touched, no row is written, and a deployment that never
configures ``FACTORY_CREDENTIAL_MASTER_KEY`` simply has an empty table and an endpoint that answers
503.  That is deliberate — the migration is not the switch, the master key is.

Revision ID: 0003_cp_secrets
Revises: 0002_cp_identity
Create Date: 2026-10-04 03:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_cp_secrets"
down_revision: str | None = "0002_cp_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.String(length=64), nullable=False),
        # the reference an environment or provider row stores the tail of (db://<name>)
        sa.Column("name", sa.String(length=64), nullable=False),
        # v1$salt$nonce$sealed$key-fingerprint — never the value itself (FR-SEC-006)
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("algo", sa.String(length=64), nullable=False),
        # first bytes of sha256(derived key): a changed master key is detectable without decrypting
        sa.Column("key_fingerprint", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_secrets")),
        sa.UniqueConstraint("name", name="uq_cp_secret_name"),
        schema="cp",
    )


def downgrade() -> None:
    op.drop_table("secrets", schema="cp")
