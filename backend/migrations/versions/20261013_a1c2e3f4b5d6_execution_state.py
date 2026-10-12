"""estado de ejecución: cantidad de entrada y contadores de riesgo persistidos

Revision ID: a1c2e3f4b5d6
Revises: 078bb5d2768e
Create Date: 2026-10-13 08:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2e3f4b5d6"
down_revision: str | Sequence[str] | None = "078bb5d2768e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column(
            "entry_quantity",
            sa.Numeric(precision=38, scale=18),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column("positions", "entry_quantity", server_default=None)
    op.add_column(
        "risk_state",
        sa.Column("execution_errors", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("risk_state", "execution_errors", server_default=None)
    op.add_column("risk_state", sa.Column("cooldown_until_day", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("risk_state", "cooldown_until_day")
    op.drop_column("risk_state", "execution_errors")
    op.drop_column("positions", "entry_quantity")
