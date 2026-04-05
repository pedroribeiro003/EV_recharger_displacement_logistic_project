"""Add unique constraint on connectors(station_id, connector_code)

Revision ID: 002
Revises: 001
Create Date: 2026-04-03
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_connectors_station_connector",
        "connectors",
        ["station_id", "connector_code"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_connectors_station_connector", "connectors", type_="unique")
