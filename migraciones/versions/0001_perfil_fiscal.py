"""perfil_fiscal: datos fiscales del receptor (una sola fila, id = 1)

Revision ID: 0001
Revises: 
Create Date: 2026-10-27 15:31:12.534938
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('perfil_fiscal',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rfc', sa.String(length=13), nullable=False),
    sa.Column('cp', sa.String(length=5), nullable=False),
    sa.Column('razon_social', sa.String(length=300), nullable=False),
    sa.Column('calle', sa.String(length=200), nullable=False),
    sa.Column('num_ext', sa.String(length=50), nullable=False),
    sa.Column('num_int', sa.String(length=50), nullable=False),
    sa.Column('referencia', sa.String(length=200), nullable=False),
    sa.Column('estado', sa.String(length=100), nullable=False),
    sa.Column('municipio', sa.String(length=100), nullable=False),
    sa.Column('colonia', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('regimen_texto', sa.String(length=200), nullable=False),
    sa.Column('actualizado', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('id = 1', name='perfil_fiscal_una_fila'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('perfil_fiscal')
