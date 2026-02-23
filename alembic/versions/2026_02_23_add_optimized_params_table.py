"""add optimized_params table

Revision ID: a3f8c2d91e47
Revises: 4854af26a1fe
Create Date: 2026-02-23 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a3f8c2d91e47'
down_revision: Union[str, None] = '4854af26a1fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('optimized_params',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('strategy_id', sa.BigInteger(), nullable=False),
        sa.Column('strategy_name', sa.String(length=50), nullable=False),
        sa.Column('params', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('win_rate', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('profit_factor', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('sharpe_ratio', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('expectancy', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('total_trades', sa.Integer(), nullable=False),
        sa.Column('wfe_ratio', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('is_overfitted', sa.Boolean(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('combinations_tested', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_optimized_params_active', 'optimized_params', ['strategy_name', 'is_active'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_optimized_params_active', table_name='optimized_params')
    op.drop_table('optimized_params')
