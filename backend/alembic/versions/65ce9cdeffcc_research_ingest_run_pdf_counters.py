"""research ingest run pdf counters

Revision ID: 65ce9cdeffcc
Revises: 2aa5b5092d0b
Create Date: 2026-11-23 19:11:05.938461
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "65ce9cdeffcc"
down_revision: str | None = "2aa5b5092d0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default is required, not cosmetic: existing installs already have
    # ingest-run rows, and a NOT NULL column with no default fails on those.
    # It is kept afterwards so inserts that omit the column still work.
    op.add_column(
        "research_ingest_runs",
        sa.Column("pdf_extracted", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_ingest_runs",
        sa.Column("pdf_failures", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("research_ingest_runs", "pdf_failures")
    op.drop_column("research_ingest_runs", "pdf_extracted")
