from typing import Optional

from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    total_balance: dict[str, float]  # currency -> amount
    total_balance_primary: float = 0.0  # consolidated in primary currency
    projected_balance: dict[str, float] = Field(default_factory=dict)  # current balance plus forecast rows
    projected_balance_primary: float = 0.0  # consolidated projected balance
    balance_date: str  # ISO date string, e.g. "2026-03-02"
    monthly_income: float
    monthly_expenses: float
    monthly_income_primary: float = 0.0
    monthly_expenses_primary: float = 0.0
    projected_income: float = 0.0
    projected_expenses: float = 0.0
    projected_income_primary: float = 0.0
    projected_expenses_primary: float = 0.0
    accounts_count: int
    pending_categorization: int
    pending_categorization_amount: float
    assets_value: dict[str, float] = Field(default_factory=dict)  # currency -> total asset value
    assets_value_primary: float = 0.0
    primary_currency: str = "USD"
    # Net pending balance from group splits (in primary currency).
    # Negative = the user is a net debtor (others paid for them, debt
    # owed). Positive = the user is a net creditor (paid for others,
    # waiting to be paid back). Computed from group balance lines so
    # it already accounts for any partial settlements.
    pending_shares_net: float = 0.0


class SpendingByCategory(BaseModel):
    category_id: Optional[str]
    category_name: str
    category_icon: str
    category_color: str
    # Posted-only, so the breakdown sums to the expenses card.
    total: float
    # Actual plus forecast, mirroring the card's projected sub-line.
    projected_total: float = 0.0
    percentage: float


class MonthlyTrend(BaseModel):
    month: str  # "2026-01"
    income: float
    expenses: float


class DailyBalance(BaseModel):
    day: int
    balance: Optional[float]  # None for future days beyond cutoff


class BalanceHistory(BaseModel):
    current: list[DailyBalance]
    previous: list[DailyBalance]


class ProjectedTransaction(BaseModel):
    recurring_id: str
    account_id: Optional[str] = None
    description: str
    amount: float
    amount_primary: Optional[float] = None
    currency: str
    type: str  # debit, credit
    date: str  # YYYY-MM-DD
    category_id: Optional[str]
    category_name: Optional[str]
    category_icon: Optional[str]
    category_color: Optional[str] = None


class SafeToSpend(BaseModel):
    """What is left to spend until the end of the month, in primary currency."""
    available_now: float  # checking + savings today
    upcoming_income: float  # recurring, pending and invoice credits still due this month
    upcoming_expenses: float  # recurring, pending and invoice debits still due this month
    card_bills_due: float  # card bills due before month end (capped at the debt)
    safe_to_spend: float  # available + income - expenses - card bills
    days_left: int  # days from today until the next month starts
    daily_allowance: Optional[float] = None  # safe_to_spend per remaining day; None when nothing is left


class SpendPace(BaseModel):
    spent: float
    reference: Optional[str]  # "budget" | "average" | None
    reference_amount: Optional[float]
    projected_month_end: Optional[float]  # linear projection at the current daily rate
    status: str  # on_track | warning | over | no_reference
    exhaustion_date: Optional[str]  # YYYY-MM-DD the reference runs out, when before month end
    days_elapsed: int
    days_in_month: int


class MonthOutlook(BaseModel):
    primary_currency: str
    is_current_month: bool
    safe_to_spend: Optional[SafeToSpend] = None
    pace: Optional[SpendPace] = None


class CommitmentItem(BaseModel):
    description: str
    amount: float  # primary currency, summed over the month
    kind: str  # "recurring" | "installment"
    installment: Optional[str] = None  # "3/12" for the latest installment seen this month


class Commitments(BaseModel):
    """Fixed outflows of a month: recurring rules plus installment purchases."""
    primary_currency: str
    recurring_total: float
    installments_total: float
    fixed_total: float
    items: list[CommitmentItem]  # largest first, capped


# --- Insights ---------------------------------------------------------------

class InstallmentMonth(BaseModel):
    month: str  # YYYY-MM
    amount: float


class InstallmentsOutlook(BaseModel):
    primary_currency: str
    remaining_total: float
    active_purchases: int
    last_month: Optional[str] = None  # last month that still has an installment
    timeline: list[InstallmentMonth]


class CategoryVariance(BaseModel):
    category_id: Optional[str]
    category_name: str
    category_icon: str
    category_color: str
    current: float
    average: float  # same day-range of the previous months
    delta: float
    delta_pct: Optional[float] = None


class CategoryVarianceReport(BaseModel):
    primary_currency: str
    reference_months: int
    increases: list[CategoryVariance]
    decreases: list[CategoryVariance]


class EmergencyFund(BaseModel):
    primary_currency: str
    liquid_total: float
    avg_monthly_expenses: float
    months_covered: Optional[float] = None
    fixed_monthly: float = 0.0
    months_covered_fixed: Optional[float] = None


class GoalPace(BaseModel):
    id: str
    name: str
    currency: str
    icon: Optional[str] = None
    color: Optional[str] = None
    percentage: float
    remaining: float
    target_date: Optional[str] = None
    required_monthly: Optional[float] = None
    actual_monthly: float
    projected_date: Optional[str] = None
    on_track: Optional[str] = None


class TopPayee(BaseModel):
    name: str
    total: float
    count: int
    is_new: bool


class TopPayees(BaseModel):
    primary_currency: str
    total_spent: float
    items: list[TopPayee]


class SubscriptionItem(BaseModel):
    description: str
    monthly_cost: float
    frequency: str
    price_change_pct: Optional[float] = None  # latest charge vs the previous one


class Subscriptions(BaseModel):
    primary_currency: str
    monthly_total: float
    yearly_total: float
    items: list[SubscriptionItem]


class InvestmentAllocation(BaseModel):
    name: str
    value: float


class InvestmentsSummary(BaseModel):
    primary_currency: str
    total_value: float
    total_cost: float
    unrealized_gain: float
    unrealized_pct: Optional[float] = None
    realized_gain: float
    allocation: list[InvestmentAllocation]


class Anomaly(BaseModel):
    kind: str  # "unusual_amount" | "possible_duplicate"
    transaction_id: str
    description: str
    amount: float
    date: str
    category_name: Optional[str] = None
    detail: Optional[float] = None  # typical amount for unusual_amount


class WeekdaySpending(BaseModel):
    weekday: int  # 0 = Monday
    total: float
    average: float  # per occurrence of that weekday in the window


class SpendingByWeekday(BaseModel):
    primary_currency: str
    days: int
    items: list[WeekdaySpending]


class HealthMetrics(BaseModel):
    primary_currency: str
    savings_rate_3m: Optional[float] = None  # percent, last 3 full months
    avg_daily_expense: Optional[float] = None  # last 3 full months
    fixed_coverage: Optional[float] = None  # income / fixed commitments this month
    top3_concentration: Optional[float] = None  # percent of this month's spend
    net_worth_change_30d: Optional[float] = None
    net_worth_change_30d_pct: Optional[float] = None
    net_worth_change_12m: Optional[float] = None
    net_worth_change_12m_pct: Optional[float] = None
    card_debt_to_income: Optional[float] = None  # percent of monthly income
    ai_categorized_pct: Optional[float] = None  # AI-categorized share of the last 90 days
