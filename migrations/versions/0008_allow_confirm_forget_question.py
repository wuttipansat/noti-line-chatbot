"""allow confirm forget question

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30 22:54:53.731618

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NAME = "ck_conversation_states_kind_allowed"


def upgrade() -> None:
    """Upgrade schema."""
    # autogenerate cannot see CHECK changes: replace the constraint by hand.
    op.drop_constraint(op.f(NAME), "conversation_states", type_="check")
    op.create_check_constraint(
        op.f(NAME), "conversation_states", "kind IN ('clarify_time', 'confirm_forget')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM conversation_states WHERE kind = 'confirm_forget'")
    op.drop_constraint(op.f(NAME), "conversation_states", type_="check")
    op.create_check_constraint(
        op.f(NAME), "conversation_states", "kind IN ('clarify_time')"
    )
