import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip as RechartsTooltip, XAxis, YAxis } from 'recharts'
import { Skeleton } from '@/components/ui/skeleton'
import { dashboard } from '@/lib/api'
import { formatCurrency } from '@/lib/format'

interface Props {
  month: string // YYYY-MM-DD, first day of the month on screen
  accountIds?: string[]
  locale: string
  dateLocale: string
  /** The user's primary currency, for cards whose response carries none. */
  currency: string
  mask: (value: string) => string
}

type Money = (value: number, currency: string) => string

function Shell({ title, children, loading }: { title: string; children: ReactNode; loading: boolean }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm px-5 py-4 min-w-0">
      <p className="text-xs font-semibold text-muted-foreground mb-2">{title}</p>
      {loading ? <Skeleton className="h-24 w-full" /> : children}
    </div>
  )
}

function Row({ left, right, tone }: { left: ReactNode; right: ReactNode; tone?: 'bad' | 'good' | 'warn' }) {
  const color = tone === 'bad' ? 'text-rose-500' : tone === 'good' ? 'text-emerald-600 dark:text-emerald-500' : tone === 'warn' ? 'text-amber-600 dark:text-amber-500' : 'text-foreground'
  return (
    <li className="flex items-center justify-between gap-3 text-xs">
      <span className="truncate text-muted-foreground">{left}</span>
      <span className={`tabular-nums shrink-0 ${color}`}>{right}</span>
    </li>
  )
}

const monthLabel = (ym: string, dateLocale: string) =>
  new Date(`${ym}-01T00:00:00`).toLocaleDateString(dateLocale, { month: 'short', year: '2-digit' })

// --- 6. Trend -----------------------------------------------------------------

function TrendCard({ accountIds, dateLocale, money, currency }: Pick<Props, 'accountIds' | 'dateLocale'> & { money: Money; currency: string }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', 'monthly-trend', 12, accountIds],
    queryFn: () => dashboard.monthlyTrend(12, accountIds),
  })
  const points = (data ?? []).map((p) => ({
    label: monthLabel(p.month, dateLocale),
    income: p.income,
    expenses: p.expenses,
    savingsRate: p.income > 0 ? Math.round(((p.income - p.expenses) / p.income) * 100) : null,
  }))
  if (!isLoading && points.length === 0) return null
  return (
    <Shell title={t('dashboard.insights.trend.title')} loading={isLoading}>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
            <YAxis yAxisId="money" hide />
            <YAxis yAxisId="rate" hide domain={[-100, 100]} />
            <RechartsTooltip
              formatter={(value, name) =>
                name === 'savingsRate' ? [`${value}%`, t('dashboard.insights.trend.savingsRate')] : [money(Number(value), currency), name === 'income' ? t('dashboard.monthlyIncome') : t('dashboard.monthlyExpenses')]
              }
            />
            <Bar yAxisId="money" dataKey="income" fill="var(--chart-3)" radius={[3, 3, 0, 0]} />
            <Bar yAxisId="money" dataKey="expenses" fill="var(--chart-5)" radius={[3, 3, 0, 0]} />
            <Line yAxisId="rate" dataKey="savingsRate" stroke="var(--chart-1)" strokeWidth={2} dot={false} connectNulls />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Shell>
  )
}

// --- 5. Installments ----------------------------------------------------------

function InstallmentsCard({ accountIds, dateLocale, money }: Pick<Props, 'accountIds' | 'dateLocale'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', 'installments', accountIds],
    queryFn: () => dashboard.installments(accountIds),
  })
  if (!isLoading && (!data || data.remaining_total <= 0)) return null
  const max = Math.max(...(data?.timeline.map((m) => m.amount) ?? [1]), 1)
  return (
    <Shell title={t('dashboard.insights.installments.title')} loading={isLoading}>
      {data && (
        <>
          <p className="text-2xl font-bold tabular-nums text-foreground">{money(data.remaining_total, data.primary_currency)}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {t('dashboard.insights.installments.caption', {
              purchases: data.active_purchases,
              month: data.last_month ? monthLabel(data.last_month, dateLocale) : '—',
            })}
          </p>
          <ul className="mt-3 space-y-1.5">
            {data.timeline.slice(0, 6).map((m) => (
              <li key={m.month} className="flex items-center gap-2 text-[11px]">
                <span className="w-12 text-muted-foreground shrink-0">{monthLabel(m.month, dateLocale)}</span>
                <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                  <div className="h-full bg-primary rounded-full" style={{ width: `${(m.amount / max) * 100}%` }} />
                </div>
                <span className="tabular-nums text-foreground w-20 text-right shrink-0">{money(m.amount, data.primary_currency)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Shell>
  )
}

// --- 7. Category variance -----------------------------------------------------

function CategoryVarianceCard({ accountIds, money }: Pick<Props, 'accountIds'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', 'category-variance', accountIds],
    queryFn: () => dashboard.categoryVariance(accountIds),
  })
  if (!isLoading && (!data || (data.increases.length === 0 && data.decreases.length === 0))) return null
  const pct = (v: number | null) => (v == null ? t('dashboard.insights.variance.new') : `${v > 0 ? '+' : ''}${Math.round(v)}%`)
  return (
    <Shell title={t('dashboard.insights.variance.title')} loading={isLoading}>
      {data && (
        <>
          <p className="text-[11px] text-muted-foreground mb-2">{t('dashboard.insights.variance.caption', { months: data.reference_months })}</p>
          <ul className="space-y-1">
            {data.increases.slice(0, 3).map((c) => (
              <Row key={`up-${c.category_id}`} left={c.category_name} tone="bad" right={`+${money(c.delta, data.primary_currency)} (${pct(c.delta_pct)})`} />
            ))}
            {data.decreases.slice(0, 3).map((c) => (
              <Row key={`down-${c.category_id}`} left={c.category_name} tone="good" right={`−${money(Math.abs(c.delta), data.primary_currency)} (${pct(c.delta_pct)})`} />
            ))}
          </ul>
        </>
      )}
    </Shell>
  )
}

// --- 8. Emergency fund --------------------------------------------------------

function EmergencyFundCard({ accountIds, money }: Pick<Props, 'accountIds'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', 'emergency-fund', accountIds],
    queryFn: () => dashboard.emergencyFund(accountIds),
  })
  if (!isLoading && (!data || data.months_covered == null)) return null
  const months = data?.months_covered ?? 0
  // Six months is the usual target; under three is the zone worth flagging.
  const tone = months >= 6 ? 'text-emerald-600 dark:text-emerald-500' : months >= 3 ? 'text-amber-600 dark:text-amber-500' : 'text-rose-500'
  return (
    <Shell title={t('dashboard.insights.reserve.title')} loading={isLoading}>
      {data && (
        <>
          <p className={`text-2xl font-bold tabular-nums ${tone}`}>{t('dashboard.insights.reserve.months', { months: months.toFixed(1) })}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {t('dashboard.insights.reserve.caption', { liquid: money(data.liquid_total, data.primary_currency), avg: money(data.avg_monthly_expenses, data.primary_currency) })}
          </p>
          {data.months_covered_fixed != null && (
            <p className="text-[11px] text-muted-foreground mt-2">
              {t('dashboard.insights.reserve.fixedOnly', { months: data.months_covered_fixed.toFixed(1) })}
            </p>
          )}
        </>
      )}
    </Shell>
  )
}

// --- 9. Goals pace ------------------------------------------------------------

function GoalsPaceCard({ dateLocale, money }: Pick<Props, 'dateLocale'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({ queryKey: ['dashboard', 'goals-pace'], queryFn: () => dashboard.goalsPace() })
  if (!isLoading && (!data || data.length === 0)) return null
  const fmtDate = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(dateLocale, { month: 'short', year: 'numeric' })
  return (
    <Shell title={t('dashboard.insights.goals.title')} loading={isLoading}>
      <ul className="space-y-3">
        {data?.map((g) => {
          const behind = g.on_track === 'behind' || g.on_track === 'overdue'
          return (
            <li key={g.id}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-foreground truncate">{g.name}</span>
                <span className="text-xs tabular-nums text-muted-foreground">{Math.round(g.percentage)}%</span>
              </div>
              <div className="h-1.5 rounded-full bg-muted mt-1 overflow-hidden" role="progressbar" aria-label={g.name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(g.percentage, 100))}>
                <div className={`h-full rounded-full ${behind ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(g.percentage, 100)}%` }} />
              </div>
              <p className="text-[11px] text-muted-foreground mt-1">
                {g.required_monthly != null
                  ? t('dashboard.insights.goals.pace', { actual: money(g.actual_monthly, g.currency), required: money(g.required_monthly, g.currency) })
                  : t('dashboard.insights.goals.paceNoDate', { actual: money(g.actual_monthly, g.currency) })}
                {g.projected_date && ` · ${t('dashboard.insights.goals.projected', { date: fmtDate(g.projected_date) })}`}
              </p>
            </li>
          )
        })}
      </ul>
    </Shell>
  )
}

// --- 10. Top payees -----------------------------------------------------------

function TopPayeesCard({ month, accountIds, money }: Pick<Props, 'month' | 'accountIds'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', 'top-payees', month, accountIds],
    queryFn: () => dashboard.topPayees(month, accountIds),
  })
  if (!isLoading && (!data || data.items.length === 0)) return null
  return (
    <Shell title={t('dashboard.insights.payees.title')} loading={isLoading}>
      {data && (
        <ul className="space-y-1.5">
          {data.items.slice(0, 6).map((p) => (
            <Row
              key={p.name}
              left={<>{p.name}{p.is_new && <span className="ml-1.5 rounded bg-primary/10 text-primary px-1 text-[10px]">{t('dashboard.insights.payees.new')}</span>}</>}
              right={`${money(p.total, data.primary_currency)} · ${data.total_spent > 0 ? Math.round((p.total / data.total_spent) * 100) : 0}%`}
            />
          ))}
        </ul>
      )}
    </Shell>
  )
}

// --- 11. Subscriptions --------------------------------------------------------

function SubscriptionsCard({ money }: { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({ queryKey: ['dashboard', 'subscriptions'], queryFn: () => dashboard.subscriptions() })
  if (!isLoading && (!data || data.items.length === 0)) return null
  return (
    <Shell title={t('dashboard.insights.subscriptions.title')} loading={isLoading}>
      {data && (
        <>
          <p className="text-2xl font-bold tabular-nums text-foreground">{money(data.monthly_total, data.primary_currency)}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">{t('dashboard.insights.subscriptions.yearly', { amount: money(data.yearly_total, data.primary_currency) })}</p>
          <ul className="mt-3 space-y-1.5">
            {data.items.slice(0, 5).map((s) => (
              <Row
                key={s.description}
                left={<>{s.description}{s.price_change_pct != null && Math.abs(s.price_change_pct) >= 1 && (
                  <span className={`ml-1.5 text-[10px] ${s.price_change_pct > 0 ? 'text-rose-500' : 'text-emerald-600'}`}>
                    {s.price_change_pct > 0 ? '▲' : '▼'} {Math.abs(Math.round(s.price_change_pct))}%
                  </span>
                )}</>}
                right={money(s.monthly_cost, data.primary_currency)}
              />
            ))}
          </ul>
        </>
      )}
    </Shell>
  )
}

// --- 12. Investments ----------------------------------------------------------

function InvestmentsCard({ money }: { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({ queryKey: ['dashboard', 'investments'], queryFn: () => dashboard.investments() })
  if (!isLoading && (!data || data.total_value <= 0)) return null
  const gain = data?.unrealized_gain ?? 0
  return (
    <Shell title={t('dashboard.insights.investments.title')} loading={isLoading}>
      {data && (
        <>
          <p className="text-2xl font-bold tabular-nums text-foreground">{money(data.total_value, data.primary_currency)}</p>
          <p className={`text-xs mt-0.5 tabular-nums ${gain >= 0 ? 'text-emerald-600 dark:text-emerald-500' : 'text-rose-500'}`}>
            {gain >= 0 ? '+' : '−'}{money(Math.abs(gain), data.primary_currency)}
            {data.unrealized_pct != null && ` (${data.unrealized_pct >= 0 ? '+' : ''}${data.unrealized_pct.toFixed(1)}%)`}
          </p>
          {data.realized_gain !== 0 && (
            <p className="text-[11px] text-muted-foreground mt-0.5">{t('dashboard.insights.investments.realized', { amount: money(data.realized_gain, data.primary_currency) })}</p>
          )}
          <ul className="mt-3 space-y-1.5">
            {data.allocation.slice(0, 5).map((a) => (
              <Row key={a.name} left={a.name} right={`${Math.round((a.value / data.total_value) * 100)}%`} />
            ))}
          </ul>
        </>
      )}
    </Shell>
  )
}

// --- 13. Anomalies ------------------------------------------------------------

function AnomaliesCard({ accountIds, dateLocale, money, currency }: Pick<Props, 'accountIds' | 'dateLocale'> & { money: Money; currency: string }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({ queryKey: ['dashboard', 'anomalies', accountIds], queryFn: () => dashboard.anomalies(accountIds) })
  if (!isLoading && (!data || data.length === 0)) return null
  return (
    <Shell title={t('dashboard.insights.anomalies.title')} loading={isLoading}>
      <ul className="space-y-2">
        {data?.map((a) => (
          <li key={a.transaction_id} className="text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="truncate text-foreground">{a.description}</span>
              <span className="tabular-nums text-rose-500 shrink-0">{money(a.amount, currency)}</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              {new Date(`${a.date}T00:00:00`).toLocaleDateString(dateLocale)} ·{' '}
              {a.kind === 'possible_duplicate'
                ? t('dashboard.insights.anomalies.duplicate')
                : t('dashboard.insights.anomalies.unusual', { typical: money(a.detail ?? 0, currency), category: a.category_name ?? '' })}
            </p>
          </li>
        ))}
      </ul>
    </Shell>
  )
}

// --- 14. Weekday heatmap ------------------------------------------------------

function WeekdayCard({ accountIds, dateLocale, money }: Pick<Props, 'accountIds' | 'dateLocale'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({ queryKey: ['dashboard', 'weekday', accountIds], queryFn: () => dashboard.spendingByWeekday(accountIds) })
  const max = Math.max(...(data?.items.map((i) => i.average) ?? [0]), 0)
  if (!isLoading && max <= 0) return null
  // 2024-01-01 was a Monday, so index 0 lines up with the API's weekday numbering.
  const dayName = (w: number) => new Date(2024, 0, 1 + w).toLocaleDateString(dateLocale, { weekday: 'short' })
  return (
    <Shell title={t('dashboard.insights.weekday.title')} loading={isLoading}>
      {data && (
        <>
          <div className="grid grid-cols-7 gap-1.5">
            {data.items.map((i) => (
              <div key={i.weekday} className="text-center" title={money(i.average, data.primary_currency)}>
                <div
                  className="h-10 rounded-md bg-primary"
                  style={{ opacity: max > 0 ? 0.12 + 0.88 * (i.average / max) : 0.12 }}
                  aria-label={`${dayName(i.weekday)}: ${money(i.average, data.primary_currency)}`}
                />
                <span className="text-[10px] text-muted-foreground">{dayName(i.weekday)}</span>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">{t('dashboard.insights.weekday.caption', { days: data.days })}</p>
        </>
      )}
    </Shell>
  )
}

// --- Derived health metrics ---------------------------------------------------

function HealthMetricsCard({ accountIds, money }: Pick<Props, 'accountIds'> & { money: Money }) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({ queryKey: ['dashboard', 'health-metrics', accountIds], queryFn: () => dashboard.healthMetrics(accountIds) })
  const pct = (v: number) => `${v.toFixed(0)}%`
  const signed = (v: number, c: string) => `${v >= 0 ? '+' : '−'}${money(Math.abs(v), c)}`
  const rows: { label: string; value: string; tone?: 'bad' | 'good' | 'warn' }[] = []
  if (data) {
    const c = data.primary_currency
    if (data.savings_rate_3m != null) rows.push({ label: t('dashboard.insights.health.savings3m'), value: pct(data.savings_rate_3m), tone: data.savings_rate_3m < 0 ? 'bad' : undefined })
    if (data.avg_daily_expense != null) rows.push({ label: t('dashboard.insights.health.dailyExpense'), value: money(data.avg_daily_expense, c) })
    if (data.fixed_coverage != null) rows.push({ label: t('dashboard.insights.health.fixedCoverage'), value: `${data.fixed_coverage.toFixed(1)}×`, tone: data.fixed_coverage < 1.5 ? 'warn' : undefined })
    if (data.top3_concentration != null) rows.push({ label: t('dashboard.insights.health.concentration'), value: pct(data.top3_concentration) })
    if (data.net_worth_change_30d != null) rows.push({ label: t('dashboard.insights.health.netWorth30d'), value: signed(data.net_worth_change_30d, c), tone: data.net_worth_change_30d < 0 ? 'bad' : 'good' })
    if (data.net_worth_change_12m != null) rows.push({ label: t('dashboard.insights.health.netWorth12m'), value: signed(data.net_worth_change_12m, c), tone: data.net_worth_change_12m < 0 ? 'bad' : 'good' })
    if (data.card_debt_to_income != null && data.card_debt_to_income > 0) rows.push({ label: t('dashboard.insights.health.cardDebt'), value: pct(data.card_debt_to_income), tone: data.card_debt_to_income > 50 ? 'bad' : undefined })
    if (data.ai_categorized_pct != null) rows.push({ label: t('dashboard.insights.health.aiShare'), value: pct(data.ai_categorized_pct) })
  }
  if (!isLoading && rows.length === 0) return null
  return (
    <Shell title={t('dashboard.insights.health.title')} loading={isLoading}>
      <ul className="space-y-1.5">
        {rows.map((r) => <Row key={r.label} left={r.label} right={r.value} tone={r.tone} />)}
      </ul>
    </Shell>
  )
}

export function DashboardInsights({ month, accountIds, locale, dateLocale, currency, mask }: Props) {
  const { t } = useTranslation()
  const money: Money = (value, cur) => mask(formatCurrency(value, cur, locale))

  return (
    <section className="mb-5" aria-label={t('dashboard.insights.title')}>
      <h2 className="text-sm font-semibold text-foreground mb-3">{t('dashboard.insights.title')}</h2>
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        <TrendCard accountIds={accountIds} dateLocale={dateLocale} money={money} currency={currency} />
        <HealthMetricsCard accountIds={accountIds} money={money} />
        <EmergencyFundCard accountIds={accountIds} money={money} />
        <InstallmentsCard accountIds={accountIds} dateLocale={dateLocale} money={money} />
        <CategoryVarianceCard accountIds={accountIds} money={money} />
        <GoalsPaceCard dateLocale={dateLocale} money={money} />
        <TopPayeesCard month={month} accountIds={accountIds} money={money} />
        <SubscriptionsCard money={money} />
        <InvestmentsCard money={money} />
        <AnomaliesCard accountIds={accountIds} dateLocale={dateLocale} money={money} currency={currency} />
        <WeekdayCard accountIds={accountIds} dateLocale={dateLocale} money={money} />
      </div>
    </section>
  )
}
