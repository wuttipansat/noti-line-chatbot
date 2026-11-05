"""allow confirm delete question

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NAME = "ck_conversation_states_kind_allowed"
KINDS = "'clarify_time', 'confirm_forget', 'confirm_delete'"


def upgrade() -> None:
    """Upgrade schema."""
    # autogenerate cannot see CHECK changes: replace the constraint by hand.
    op.drop_constraint(op.f(NAME), "conversation_states", type_="check")
    op.create_check_constraint(
        op.f(NAME), "conversation_states", f"kind IN ({KINDS})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM conversation_states WHERE kind = 'confirm_delete'")
    op.drop_constraint(op.f(NAME), "conversation_states", type_="check")
    op.create_check_constraint(
        op.f(NAME),
        "conversation_states",
        "kind IN ('clarify_time', 'confirm_forget')",
    )
