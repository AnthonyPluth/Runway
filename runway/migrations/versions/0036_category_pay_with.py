"""The card or account a category's spending goes on belongs to the category, not its budget: any category can have one
(budgeted or not, a subcategory too), and the budget forecast spends a subcategory's budget on its own. Each budget's
choice moves to its category, and the budget's column goes, so there's one place it's kept.

Revision ID: 0036
Revises: 0035
"""
import sqlalchemy as sa
from alembic import op

revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'pay_with' not in {c['name'] for c in insp.get_columns('categories')}:
        op.add_column('categories', sa.Column('pay_with', sa.Text()))
    if 'pay_with' in {c['name'] for c in insp.get_columns('budgets')}:
        op.execute("UPDATE categories SET pay_with = (SELECT b.pay_with FROM budgets b WHERE b.category = categories.name) "
                   "WHERE pay_with IS NULL AND EXISTS "
                   "(SELECT 1 FROM budgets b WHERE b.category = categories.name AND b.pay_with IS NOT NULL)")
        with op.batch_alter_table('budgets') as batch:
            batch.drop_column('pay_with')


def downgrade() -> None:
    op.add_column('budgets', sa.Column('pay_with', sa.Text()))
    # Only a budgeted category's choice has somewhere to go back to.
    op.execute("UPDATE budgets SET pay_with = (SELECT c.pay_with FROM categories c WHERE c.name = budgets.category)")
    with op.batch_alter_table('categories') as batch:
        batch.drop_column('pay_with')
