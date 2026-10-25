"""Turn a report into a downloadable table (CSV for spreadsheets, PDF to print).

Every report is first reduced to a ``ReportTable``; the two writers only know
tables. That keeps what a file says identical between formats and means a new
report needs one builder, not two writers.

ReportLab rather than an HTML-to-PDF engine, for the reason ``invoice_pdf``
gives: it ships pure-Python wheels, so the server image needs no native libs.
"""
import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.schemas.report import (
    AnnualSummary,
    BudgetReport,
    CategoryReport,
    ReportResponse,
    TaxSummary,
)
from app.services.report_filters import ReportFilters
from app.services.report_tables_service import _conditions, _context, resolve_window

MAX_EXPORT_ROWS = 50_000

_LABELS = {
    "en": {
        "date": "Date", "description": "Description", "payee": "Payee", "category": "Category",
        "group": "Group", "account": "Account", "type": "Type", "amount": "Amount",
        "currency": "Currency", "status": "Status", "notes": "Notes", "income": "Income",
        "expenses": "Expenses", "net": "Net", "total": "Total", "share": "Share %",
        "count": "Transactions", "previous": "Previous period", "change": "Change %",
        "period": "Period", "budget": "Budget", "actual": "Actual", "difference": "Difference",
        "credit": "Credit", "debit": "Debit", "uncategorized": "Uncategorized", "no_payee": "(no payee)",
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "t_categories": "Spending by category", "t_income_categories": "Income by category",
        "t_annual": "Annual summary", "t_tax": "Income and expenses by category and payee",
        "t_budget": "Budget vs actual", "t_ie": "Income and expenses", "t_transactions": "Transactions",
    },
    "pt": {
        "date": "Data", "description": "Descrição", "payee": "Beneficiário", "category": "Categoria",
        "group": "Grupo", "account": "Conta", "type": "Tipo", "amount": "Valor",
        "currency": "Moeda", "status": "Situação", "notes": "Observações", "income": "Receitas",
        "expenses": "Despesas", "net": "Saldo", "total": "Total", "share": "% do total",
        "count": "Transações", "previous": "Período anterior", "change": "Variação %",
        "period": "Período", "budget": "Orçado", "actual": "Realizado", "difference": "Diferença",
        "credit": "Crédito", "debit": "Débito", "uncategorized": "Sem categoria", "no_payee": "(sem beneficiário)",
        "months": ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"],
        "t_categories": "Gastos por categoria", "t_income_categories": "Receitas por categoria",
        "t_annual": "Resumo anual", "t_tax": "Receitas e despesas por categoria e beneficiário",
        "t_budget": "Orçado contra realizado", "t_ie": "Receitas e despesas", "t_transactions": "Transações",
    },
    "es": {
        "date": "Fecha", "description": "Descripción", "payee": "Beneficiario", "category": "Categoría",
        "group": "Grupo", "account": "Cuenta", "type": "Tipo", "amount": "Importe",
        "currency": "Moneda", "status": "Estado", "notes": "Notas", "income": "Ingresos",
        "expenses": "Gastos", "net": "Neto", "total": "Total", "share": "% del total",
        "count": "Transacciones", "previous": "Periodo anterior", "change": "Variación %",
        "period": "Periodo", "budget": "Presupuesto", "actual": "Real", "difference": "Diferencia",
        "credit": "Crédito", "debit": "Débito", "uncategorized": "Sin categoría", "no_payee": "(sin beneficiario)",
        "months": ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"],
        "t_categories": "Gasto por categoría", "t_income_categories": "Ingresos por categoría",
        "t_annual": "Resumen anual", "t_tax": "Ingresos y gastos por categoría y beneficiario",
        "t_budget": "Presupuesto frente a real", "t_ie": "Ingresos y gastos", "t_transactions": "Transacciones",
    },
}


def labels(lang: Optional[str]) -> dict:
    return _LABELS.get((lang or "en").split("-")[0].lower(), _LABELS["en"])


@dataclass
class ReportTable:
    title: str
    subtitle: str
    columns: list[str]
    rows: list[list]
    numeric_columns: set[int] = field(default_factory=set)
    #: Rows printed in bold (totals).
    bold_rows: set[int] = field(default_factory=set)


# --- Writers ----------------------------------------------------------------

def _plain(value, decimal: str) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.2f}"
        return text.replace(".", decimal) if decimal != "." else text
    return str(value)


def to_csv(table: ReportTable, delimiter: str = ",", decimal: str = ".") -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(table.columns)
    for row in table.rows:
        writer.writerow([_plain(v, decimal) for v in row])
    # The BOM makes Excel read the file as UTF-8 instead of guessing a codepage.
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def _grouped(value: float) -> str:
    return f"{value:,.2f}"


def to_pdf(table: ReportTable) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    wide = len(table.columns) > 6
    page = landscape(A4) if wide else A4
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, title=table.title,
    )
    styles = getSampleStyleSheet()
    font_size = 7 if len(table.columns) > 9 else 8
    cell = styles["BodyText"].clone("cell", fontSize=font_size, leading=font_size + 2)

    header = [Paragraph(f"<b>{_escape(c)}</b>", cell) for c in table.columns]
    data = [header]
    for row in table.rows:
        data.append([
            _grouped(v) if isinstance(v, float) else Paragraph(_escape("" if v is None else str(v)), cell)
            for v in row
        ])

    style = [
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for col in table.numeric_columns:
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    for index in table.bold_rows:
        style.append(("FONTNAME", (0, index + 1), (-1, index + 1), "Helvetica-Bold"))

    flow = [
        Paragraph(_escape(table.title), styles["Title"]),
        Paragraph(_escape(table.subtitle), styles["Normal"]),
        Spacer(1, 6 * mm),
        Table(data, repeatRows=1, style=TableStyle(style)),
    ]
    doc.build(flow)
    return buffer.getvalue()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- Builders ---------------------------------------------------------------

def _money(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def category_table(report: CategoryReport, lang: Optional[str]) -> ReportTable:
    L = labels(lang)
    columns = [L["category"], L["group"], L["total"], L["share"], L["count"], L["previous"], L["change"]]
    rows = [
        [i.name or L["uncategorized"], i.group_name or "", _money(i.total), _money(i.share_pct), i.count,
         _money(i.previous_total), _money(i.delta_pct)]
        for i in report.items
    ]
    rows.append([L["total"], "", _money(report.total), 100.0 if report.total else 0.0,
                 sum(i.count for i in report.items), _money(report.previous_total), None])
    title = L["t_categories"] if report.kind == "expenses" else L["t_income_categories"]
    return ReportTable(
        title, f"{report.start} → {report.end} · {report.primary_currency}", columns, rows,
        numeric_columns={2, 3, 4, 5, 6}, bold_rows={len(rows) - 1},
    )


def annual_table(report: AnnualSummary, lang: Optional[str]) -> ReportTable:
    L = labels(lang)
    columns = [L["category"], L["type"], *L["months"], L["total"]]
    rows: list[list] = []
    for r in report.rows:
        rows.append([r.name or L["uncategorized"], L["income"] if r.kind == "income" else L["expenses"],
                     *[_money(m) for m in r.months], _money(r.total)])
    for name, months in ((L["income"], report.income_months), (L["expenses"], report.expense_months),
                         (L["net"], report.net_months)):
        rows.append([name, L["total"], *[_money(m) for m in months], _money(sum(months))])
    return ReportTable(
        f"{L['t_annual']} {report.year}", report.primary_currency, columns, rows,
        numeric_columns=set(range(2, 15)), bold_rows={len(rows) - 3, len(rows) - 2, len(rows) - 1},
    )


def tax_table(report: TaxSummary, lang: Optional[str]) -> ReportTable:
    L = labels(lang)
    columns = [L["type"], L["category"], L["payee"], L["count"], L["total"]]
    rows: list[list] = []
    bold: set[int] = set()
    for cat in report.categories:
        kind = L["income"] if cat.kind == "income" else L["expenses"]
        bold.add(len(rows))
        rows.append([kind, cat.name or L["uncategorized"], "", cat.count, _money(cat.total)])
        for payee in cat.payees:
            rows.append([kind, cat.name or L["uncategorized"], payee.name or L["no_payee"], payee.count, _money(payee.total)])
    bold.update({len(rows), len(rows) + 1})
    rows.append([L["income"], L["total"], "", None, _money(report.income_total)])
    rows.append([L["expenses"], L["total"], "", None, _money(report.expense_total)])
    return ReportTable(
        L["t_tax"], f"{report.start} → {report.end} · {report.primary_currency}", columns, rows,
        numeric_columns={3, 4}, bold_rows=bold,
    )


def budget_table(report: BudgetReport, lang: Optional[str]) -> ReportTable:
    L = labels(lang)
    columns = [L["category"], L["group"], L["budget"], L["actual"], L["difference"]]
    rows = [
        [r.name, r.group_name or "", _money(r.budget_total), _money(r.actual_total),
         _money(r.budget_total - r.actual_total)]
        for r in report.rows
    ]
    total_budget = sum(r.budget_total for r in report.rows)
    total_actual = sum(r.actual_total for r in report.rows)
    rows.append([L["total"], "", _money(total_budget), _money(total_actual), _money(total_budget - total_actual)])
    return ReportTable(
        f"{L['t_budget']} {report.year}", report.primary_currency, columns, rows,
        numeric_columns={2, 3, 4}, bold_rows={len(rows) - 1},
    )


def income_expenses_table(report: ReportResponse, lang: Optional[str]) -> ReportTable:
    L = labels(lang)
    columns = [L["period"], L["income"], L["expenses"], L["net"]]
    rows = [
        [p.date, _money(p.breakdowns.get("income", 0)), _money(p.breakdowns.get("expenses", 0)), _money(p.value)]
        for p in report.trend
    ]
    totals = {b.key: b.value for b in report.summary.breakdowns}
    rows.append([L["total"], _money(totals.get("income", 0)), _money(totals.get("expenses", 0)), _money(totals.get("netIncome", 0))])
    return ReportTable(L["t_ie"], report.meta.currency, columns, rows, numeric_columns={1, 2, 3}, bold_rows={len(rows) - 1})


async def transactions_table(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    start: Optional[date], end: Optional[date], lang: Optional[str],
    account_ids: Optional[list[uuid.UUID]] = None, filters: Optional[ReportFilters] = None,
) -> ReportTable:
    """Every transaction the report would count, one per row, for an accountant."""
    L = labels(lang)
    filters = filters or ReportFilters()
    start, end = resolve_window(start, end)
    primary, report_date = await _context(session, user_id)
    result = await session.execute(
        select(Transaction, Category, CategoryGroup, Payee, Account)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .where(*_conditions(workspace_id, report_date, start, end, account_ids, filters, None))
        .order_by(report_date, Transaction.created_at)
        .limit(MAX_EXPORT_ROWS)
    )
    columns = [L["date"], L["description"], L["payee"], L["category"], L["group"], L["account"],
               L["type"], L["amount"], L["currency"], L["notes"]]
    rows = []
    for tx, cat, group, payee, account in result.all():
        amount = float(tx.amount_primary if tx.amount_primary is not None else tx.amount)
        rows.append([
            tx.date.isoformat(), tx.description, (payee.name if payee else tx.payee) or "",
            cat.name if cat else L["uncategorized"], group.name if group else "",
            account.display_name or account.name, L["credit"] if tx.type == "credit" else L["debit"],
            _money(amount if tx.type == "credit" else -amount), primary, tx.notes or "",
        ])
    return ReportTable(L["t_transactions"], f"{start} → {end} · {primary}", columns, rows, numeric_columns={7})
