"""«Защита бюджета»: правила из шаблонов и порог в ценах заявки.

threshold_kind = 'cpa_x' — порог не в рублях, а «в N раз больше целевой цены заявки проекта»: одно правило
подходит клиентам с заявкой за 300 ₽ и за 5000 ₽. Цена заявки берётся при каждой проверке.
template — ключ шаблона из rules_templates.py; у проекта не больше одного правила на шаблон.

Revision ID: 0064_budget_protection
Revises: 0063_client_files
Create Date: 2026-11-19
"""

from alembic import op

revision = "0064_budget_protection"
down_revision = "0063_client_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation_rules ADD COLUMN threshold_kind TEXT NOT NULL DEFAULT 'rub'")
    op.execute(
        "ALTER TABLE automation_rules ADD CONSTRAINT automation_rules_threshold_kind_chk "
        "CHECK (threshold_kind IN ('rub', 'cpa_x'))"
    )
    op.execute("ALTER TABLE automation_rules ADD COLUMN template TEXT")
    op.execute(
        "CREATE UNIQUE INDEX automation_rules_template_uq ON automation_rules (scope_workspace_id, template) "
        "WHERE template IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS automation_rules_template_uq")
    op.execute("ALTER TABLE automation_rules DROP COLUMN IF EXISTS template")
    op.execute("ALTER TABLE automation_rules DROP CONSTRAINT IF EXISTS automation_rules_threshold_kind_chk")
    op.execute("ALTER TABLE automation_rules DROP COLUMN IF EXISTS threshold_kind")
