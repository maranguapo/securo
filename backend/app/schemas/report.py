from pydantic import BaseModel


class ReportBreakdown(BaseModel):
    key: str
    label: str
    value: float
    color: str


class ReportSummary(BaseModel):
    primary_value: float
    change_amount: float
    change_percent: float | None
    breakdowns: list[ReportBreakdown]


class ReportCompositionItem(BaseModel):
    key: str
    label: str
    value: float
    color: str
    group: str


class ReportDataPoint(BaseModel):
    date: str
    value: float
    breakdowns: dict[str, float]
    change: float | None = None
    composition: list[ReportCompositionItem] = []


class ReportMeta(BaseModel):
    type: str
    series_keys: list[str]
    currency: str
    interval: str
    forecast_start_date: str | None = None
    baseline_active: bool = False
    baseline_lookback_days: int | None = None
    # Cash flow only. ``runway_date`` is the first day (YYYY-MM-DD) the
    # projected balance goes below zero, or None when it stays at or above
    # zero through the horizon. ``lowest_balance`` is the minimum projected
    # balance from today to the end of the horizon, on ``lowest_balance_date``.
    runway_date: str | None = None
    lowest_balance: float | None = None
    lowest_balance_date: str | None = None


class CategoryTrendItem(BaseModel):
    key: str
    label: str
    color: str
    total: float
    group: str
    series: list[ReportDataPoint]


class ReportResponse(BaseModel):
    summary: ReportSummary
    trend: list[ReportDataPoint]
    meta: ReportMeta
    composition: list[ReportCompositionItem] = []
    category_trend: list[CategoryTrendItem] = []
    comparison: "ReportComparison | None" = None


# --- Phase 2/3: category, budget, annual and tax reports ---------------------

class ReportComparison(BaseModel):
    """The window just before the reported one, and the period's own average."""
    previous_start: str
    previous_end: str
    previous_income: float
    previous_expenses: float
    income_change_pct: float | None = None
    expenses_change_pct: float | None = None
    average_income: float  # per interval of the reported window
    average_expenses: float


class CategoryReportItem(BaseModel):
    category_id: str | None
    name: str
    color: str
    icon: str
    group_name: str | None = None
    total: float
    share_pct: float
    count: int
    previous_total: float
    delta: float
    delta_pct: float | None = None
    series: list[float]  # one value per month in CategoryReport.months


class CategoryReport(BaseModel):
    primary_currency: str
    kind: str  # "expenses" | "income"
    start: str
    end: str
    previous_start: str
    previous_end: str
    months: list[str]
    total: float
    previous_total: float
    items: list[CategoryReportItem]


class BudgetReportRow(BaseModel):
    category_id: str
    name: str
    color: str
    icon: str
    group_name: str | None = None
    budget: list[float | None]  # 12 months
    actual: list[float]  # 12 months
    budget_total: float
    actual_total: float


class BudgetReport(BaseModel):
    primary_currency: str
    year: int
    through_month: int  # last month with actuals (12 for past years)
    rows: list[BudgetReportRow]


class AnnualRow(BaseModel):
    category_id: str | None
    name: str
    color: str
    kind: str  # "income" | "expenses"
    months: list[float]  # 12 values
    total: float


class AnnualSummary(BaseModel):
    primary_currency: str
    year: int
    rows: list[AnnualRow]
    income_months: list[float]
    expense_months: list[float]
    net_months: list[float]


class TaxPayee(BaseModel):
    name: str | None  # None: no payee recorded
    total: float
    count: int


class TaxCategory(BaseModel):
    kind: str  # "income" | "expenses"
    category_id: str | None
    name: str
    total: float
    count: int
    payees: list[TaxPayee]


class TaxSummary(BaseModel):
    primary_currency: str
    start: str
    end: str
    income_total: float
    expense_total: float
    categories: list[TaxCategory]


ReportResponse.model_rebuild()
