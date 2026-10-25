import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { reports } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { localDateString } from '@/lib/date-utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { Skeleton } from '@/components/ui/skeleton'
import { DateRangePicker } from '@/components/ui/date-range-picker'
import { ReportFiltersBar } from '@/components/reports/ReportFiltersBar'
import { ExportMenu } from '@/components/reports/ExportMenu'
import { reportFilterParams, useReportFilters } from '@/lib/report-filters'
import type { AnnualSummary, BudgetReport, CategoryReport, TaxSummary } from '@/types'

export const EXTRA_REPORT_TABS = ['categories', 'budget', 'annual', 'tax'] as const
export type ExtraReportTab = (typeof EXTRA_REPORT_TABS)[number]

const CARD = 'bg-card rounded-xl border border-border shadow-sm'

type Money = (value: number) => string

function useMoney(currency: string): Money {
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  return (value) => mask(formatCurrency(value, currency, locale))
}

function ErrorLine({ error }: { error: unknown }) {
  const { t } = useTranslation()
  return <p className="text-sm text-rose-600 dark:text-rose-400 py-6 text-center">{extractApiError(error, t('reports.loadError'))}</p>
}

function Empty() {
  const { t } = useTranslation()
  return <p className="text-muted-foreground text-sm text-center py-16">{t('reports.noData')}</p>
}

function Delta({ pct, upIsBad }: { pct: number | null; upIsBad: boolean }) {
  const { t } = useTranslation()
  if (pct == null) return <span className="text-xs text-muted-foreground">{t('reports.cat.new')}</span>
  const bad = upIsBad ? pct > 0 : pct < 0
  const tone = Math.abs(pct) < 0.5 ? 'text-muted-foreground' : bad ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-500'
  return <span className={`text-xs font-semibold tabular-nums ${tone}`}>{pct > 0 ? '+' : ''}{pct.toFixed(1)}%</span>
}

function YearStepper({ year, onChange }: { year: number; onChange: (y: number) => void }) {
  return (
    <div className="inline-flex items-center rounded-lg border border-border bg-card overflow-hidden">
      <button type="button" aria-label="-1" onClick={() => onChange(year - 1)} className="px-2 py-1.5 hover:bg-muted"><ChevronLeft size={14} /></button>
      <span className="px-3 text-sm font-semibold tabular-nums">{year}</span>
      <button type="button" aria-label="+1" onClick={() => onChange(year + 1)} className="px-2 py-1.5 hover:bg-muted"><ChevronRight size={14} /></button>
    </div>
  )
}

// --- Period presets (categories) ----------------------------------------------

type PresetKey = 'thisMonth' | 'lastMonth' | 'last3Months' | 'ytd' | 'lastYear' | 'custom'

function presetRange(key: Exclude<PresetKey, 'custom'>): { from: string; to: string } {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()
  const day = (d: Date) => localDateString(d)
  switch (key) {
    case 'thisMonth': return { from: day(new Date(y, m, 1)), to: day(now) }
    case 'lastMonth': return { from: day(new Date(y, m - 1, 1)), to: day(new Date(y, m, 0)) }
    case 'last3Months': return { from: day(new Date(y, m - 2, 1)), to: day(now) }
    case 'ytd': return { from: day(new Date(y, 0, 1)), to: day(now) }
    case 'lastYear': return { from: day(new Date(y - 1, 0, 1)), to: day(new Date(y - 1, 11, 31)) }
  }
}

const PRESETS: Exclude<PresetKey, 'custom'>[] = ['thisMonth', 'lastMonth', 'last3Months', 'ytd', 'lastYear']

function PeriodPicker({ preset, from, to, onPreset, onCustom }: {
  preset: PresetKey; from: string; to: string
  onPreset: (key: Exclude<PresetKey, 'custom'>) => void
  onCustom: (from: string, to: string) => void
}) {
  const { t } = useTranslation()
  const defaults = presetRange('last3Months')
  return (
    <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
      {PRESETS.map((key) => (
        <button
          key={key}
          type="button"
          onClick={() => onPreset(key)}
          className={`px-2.5 py-1.5 text-xs font-semibold transition-colors ${
            preset === key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
          }`}
        >
          {t(`reports.period.${key}`)}
        </button>
      ))}
      <DateRangePicker
        variant="segment"
        active={preset === 'custom'}
        from={from}
        to={to}
        defaultFrom={defaults.from}
        defaultTo={defaults.to}
        maxRangeYears={2}
        onChange={(f, tt) => { if (f && tt) onCustom(f, tt) }}
        label={t('reports.customRange')}
        placeholder={t('reports.pickCustomRange')}
      />
    </div>
  )
}

// --- Categories ---------------------------------------------------------------

function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null
  const max = Math.max(...values, 1)
  const w = 80
  const h = 24
  const points = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * (h - 2) - 1}`).join(' ')
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className="shrink-0">
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

function CategoriesReportView() {
  const { t } = useTranslation()
  const { activeAccountIds } = useCollectionFilter()
  const acctIds = activeAccountIds ?? undefined
  const [filters, setFilters, clearFilters] = useReportFilters()
  const [kind, setKind] = useState<'expenses' | 'income'>('expenses')
  const [preset, setPreset] = useState<PresetKey>('thisMonth')
  const [range, setRange] = useState(() => presetRange('thisMonth'))
  const filterParams = reportFilterParams(filters)
  const noAccounts = activeAccountIds !== null && activeAccountIds.length === 0

  const { data, isLoading, error } = useQuery<CategoryReport>({
    queryKey: ['reports', 'categories', kind, range.from, range.to, activeAccountIds, filterParams],
    queryFn: () => reports.categories(range.from, range.to, kind, acctIds, filterParams),
    enabled: !noAccounts,
  })
  const money = useMoney(data?.primary_currency ?? 'USD')
  const maxShare = Math.max(...(data?.items.map((i) => i.total) ?? [0]), 1)
  const totalPct = data && data.previous_total > 0 ? ((data.total - data.previous_total) / data.previous_total) * 100 : null

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
            {(['expenses', 'income'] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                className={`px-3 py-1.5 text-xs font-semibold ${kind === k ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted/50'}`}
              >
                {t(`reports.cat.${k}`)}
              </button>
            ))}
          </div>
          <PeriodPicker
            preset={preset}
            from={range.from}
            to={range.to}
            onPreset={(key) => { setPreset(key); setRange(presetRange(key)) }}
            onCustom={(f, to) => { setPreset('custom'); setRange({ from: f, to }) }}
          />
        </div>
        <ExportMenu
          report="categories"
          withExtract
          params={{ start_date: range.from, end_date: range.to, flow: kind, ...(acctIds ? { account_ids: acctIds } : {}), ...filterParams }}
        />
      </div>

      <ReportFiltersBar value={filters} onChange={setFilters} onClear={clearFilters} />

      <div className={`${CARD} px-5 py-4 mb-5`}>
        {isLoading ? <Skeleton className="h-12 w-64" /> : (
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <p className="text-3xl font-bold tabular-nums text-foreground">{money(data?.total ?? 0)}</p>
            <span className="text-sm text-muted-foreground">
              {t('reports.cat.vsPrevious', { amount: money(data?.previous_total ?? 0) })}
            </span>
            <Delta pct={totalPct} upIsBad={kind === 'expenses'} />
          </div>
        )}
      </div>

      <div className={CARD}>
        {error ? <ErrorLine error={error} /> : isLoading ? (
          <div className="p-5 space-y-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        ) : !data || data.items.length === 0 ? <Empty /> : (
          <ul className="divide-y divide-border">
            {data.items.map((item) => (
              <li key={item.category_id ?? 'none'} className="px-5 py-3 flex items-center gap-4">
                <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: item.color }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="text-sm font-medium text-foreground truncate">
                      {item.name || t('reports.cat.uncategorized')}
                      {item.group_name && <span className="ml-2 text-xs text-muted-foreground">{item.group_name}</span>}
                    </p>
                    <p className="text-sm font-bold tabular-nums text-foreground shrink-0">{money(item.total)}</p>
                  </div>
                  <div className="h-1.5 rounded-full bg-muted mt-1.5 overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${(item.total / maxShare) * 100}%`, backgroundColor: item.color }} />
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {t('reports.cat.shareLine', { share: item.share_pct.toFixed(1), n: item.count })}
                  </p>
                </div>
                <Sparkline values={item.series} color={item.color} />
                <div className="w-32 text-right shrink-0 hidden sm:block">
                  <Delta pct={item.delta_pct} upIsBad={kind === 'expenses'} />
                  <p className="text-[11px] text-muted-foreground tabular-nums">{money(item.previous_total)}</p>
                </div>
                {item.category_id && (
                  <Link
                    to={`/transactions?category_id=${item.category_id}&from=${range.from}&to=${range.to}`}
                    className="text-xs font-medium text-primary hover:underline shrink-0"
                  >
                    {t('reports.cat.viewTransactions')}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// --- Budget vs actual ---------------------------------------------------------

function BudgetReportView() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [year, setYear] = useState(new Date().getFullYear())
  const { data, isLoading, error } = useQuery<BudgetReport>({
    queryKey: ['reports', 'budget', year],
    queryFn: () => reports.budgetVsActual(year),
  })
  const money = useMoney(data?.primary_currency ?? 'USD')
  const monthName = (i: number) => new Date(2024, i, 1).toLocaleDateString(locale, { month: 'short' })

  const totals = useMemo(() => {
    const rows = data?.rows ?? []
    // Compare like with like: only months that already have actuals.
    const through = data?.through_month ?? 0
    const budget = rows.reduce((s, r) => s + r.budget.slice(0, through).reduce<number>((a, b) => a + (b ?? 0), 0), 0)
    const actual = rows.reduce((s, r) => s + r.actual.slice(0, through).reduce((a, b) => a + b, 0), 0)
    return { budget, actual }
  }, [data])

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <YearStepper year={year} onChange={setYear} />
        <ExportMenu report="budget-vs-actual" params={{ year }} />
      </div>

      <div className={`${CARD} px-5 py-4 mb-5 grid grid-cols-1 sm:grid-cols-3 gap-4`}>
        {isLoading ? <Skeleton className="h-12 w-full sm:col-span-3" /> : (
          <>
            <Stat label={t('reports.bud.budgeted')} value={money(totals.budget)} />
            <Stat label={t('reports.bud.spent')} value={money(totals.actual)} />
            <Stat
              label={totals.budget - totals.actual >= 0 ? t('reports.bud.remaining') : t('reports.bud.over')}
              value={money(Math.abs(totals.budget - totals.actual))}
              tone={totals.budget - totals.actual >= 0 ? undefined : 'bad'}
            />
          </>
        )}
      </div>

      <div className={CARD}>
        {error ? <ErrorLine error={error} /> : isLoading ? (
          <div className="p-5 space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : !data || data.rows.length === 0 ? <p className="text-muted-foreground text-sm text-center py-16">{t('reports.bud.empty')}</p> : (
          <ul className="divide-y divide-border">
            {data.rows.map((row) => {
              const through = data.through_month
              const budget = row.budget.slice(0, through).reduce<number>((a, b) => a + (b ?? 0), 0)
              const actual = row.actual.slice(0, through).reduce((a, b) => a + b, 0)
              const pct = budget > 0 ? (actual / budget) * 100 : actual > 0 ? 100 : 0
              const over = budget > 0 && actual > budget
              return (
                <li key={row.category_id}>
                  <details className="group">
                    <summary className="px-5 py-3 flex items-center gap-4 cursor-pointer list-none">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: row.color }} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-3">
                          <p className="text-sm font-medium text-foreground truncate">{row.name}</p>
                          <p className="text-sm tabular-nums shrink-0">
                            <span className={`font-bold ${over ? 'text-rose-600 dark:text-rose-400' : 'text-foreground'}`}>{money(actual)}</span>
                            <span className="text-muted-foreground"> / {money(budget)}</span>
                          </p>
                        </div>
                        <div
                          className="h-1.5 rounded-full bg-muted mt-1.5 overflow-hidden"
                          role="progressbar" aria-label={row.name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(pct, 100))}
                        >
                          <div className={`h-full rounded-full ${over ? 'bg-rose-500' : pct >= 80 ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                        </div>
                      </div>
                    </summary>
                    <div className="px-5 pb-3 overflow-x-auto">
                      <table className="w-full text-xs tabular-nums">
                        <thead>
                          <tr className="text-muted-foreground">
                            <th className="text-left font-medium py-1">{t('reports.bud.month')}</th>
                            <th className="text-right font-medium py-1">{t('reports.bud.budgeted')}</th>
                            <th className="text-right font-medium py-1">{t('reports.bud.spent')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {row.budget.map((b, i) => (
                            <tr key={i} className={i >= through ? 'text-muted-foreground/60' : ''}>
                              <td className="py-0.5">{monthName(i)}</td>
                              <td className="text-right">{b == null ? '—' : money(b)}</td>
                              <td className={`text-right ${b != null && row.actual[i] > b ? 'text-rose-600 dark:text-rose-400' : ''}`}>{money(row.actual[i])}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'bad' }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-0.5">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${tone === 'bad' ? 'text-rose-600 dark:text-rose-400' : 'text-foreground'}`}>{value}</p>
    </div>
  )
}

// --- Annual summary -----------------------------------------------------------

function AnnualReportView() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { activeAccountIds } = useCollectionFilter()
  const acctIds = activeAccountIds ?? undefined
  const [filters, setFilters, clearFilters] = useReportFilters()
  const [year, setYear] = useState(new Date().getFullYear())
  const filterParams = reportFilterParams(filters)
  const { data, isLoading, error } = useQuery<AnnualSummary>({
    queryKey: ['reports', 'annual', year, activeAccountIds, filterParams],
    queryFn: () => reports.annualSummary(year, acctIds, filterParams),
    enabled: !(activeAccountIds !== null && activeAccountIds.length === 0),
  })
  const money = useMoney(data?.primary_currency ?? 'USD')
  const monthName = (i: number) => new Date(2024, i, 1).toLocaleDateString(locale, { month: 'short' })
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0)

  const section = (kind: 'income' | 'expenses') => {
    const rows = (data?.rows ?? []).filter((r) => r.kind === kind)
    if (rows.length === 0) return null
    return (
      <>
        <tr><td colSpan={14} className="px-3 pt-4 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t(`reports.ann.${kind}`)}</td></tr>
        {rows.map((r) => (
          <tr key={`${kind}-${r.category_id ?? 'none'}`} className="border-t border-border/60">
            <th scope="row" className="sticky left-0 bg-card text-left font-medium px-3 py-1.5 whitespace-nowrap">
              <span className="inline-block w-2 h-2 rounded-full mr-2" style={{ backgroundColor: r.color }} />
              {r.name || t('reports.cat.uncategorized')}
            </th>
            {r.months.map((m, i) => <td key={i} className="text-right px-3 tabular-nums text-muted-foreground">{m ? money(m) : '—'}</td>)}
            <td className="text-right px-3 tabular-nums font-semibold">{money(r.total)}</td>
          </tr>
        ))}
      </>
    )
  }

  const totalRow = (label: string, values: number[], bold = false) => (
    <tr className={`border-t border-border ${bold ? 'font-bold' : 'font-semibold'}`}>
      <th scope="row" className="sticky left-0 bg-card text-left px-3 py-1.5">{label}</th>
      {values.map((v, i) => <td key={i} className={`text-right px-3 tabular-nums ${bold && v < 0 ? 'text-rose-600 dark:text-rose-400' : ''}`}>{money(v)}</td>)}
      <td className="text-right px-3 tabular-nums">{money(sum(values))}</td>
    </tr>
  )

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <YearStepper year={year} onChange={setYear} />
        <ExportMenu report="annual-summary" params={{ year, ...(acctIds ? { account_ids: acctIds } : {}), ...filterParams }} />
      </div>
      <ReportFiltersBar value={filters} onChange={setFilters} onClear={clearFilters} compact />
      <div className={`${CARD} overflow-x-auto`}>
        {error ? <ErrorLine error={error} /> : isLoading ? (
          <div className="p-5"><Skeleton className="h-64 w-full" /></div>
        ) : !data || data.rows.length === 0 ? <Empty /> : (
          <table className="w-full text-xs min-w-[900px]">
            <thead>
              <tr className="text-muted-foreground">
                <th className="sticky left-0 bg-card text-left font-medium px-3 py-2">{t('reports.ann.category')}</th>
                {Array.from({ length: 12 }).map((_, i) => <th key={i} className="text-right font-medium px-3 py-2">{monthName(i)}</th>)}
                <th className="text-right font-medium px-3 py-2">{t('reports.ann.total')}</th>
              </tr>
            </thead>
            <tbody>
              {section('income')}
              {section('expenses')}
              {totalRow(t('reports.ann.income'), data.income_months)}
              {totalRow(t('reports.ann.expenses'), data.expense_months)}
              {totalRow(t('reports.ann.net'), data.net_months, true)}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// --- Tax summary --------------------------------------------------------------

function TaxReportView() {
  const { t } = useTranslation()
  const { activeAccountIds } = useCollectionFilter()
  const acctIds = activeAccountIds ?? undefined
  const [filters, setFilters, clearFilters] = useReportFilters()
  // Tax paperwork is about the year that just closed.
  const [year, setYear] = useState(new Date().getFullYear() - 1)
  const filterParams = reportFilterParams(filters)
  const start = `${year}-01-01`
  const end = `${year}-12-31`
  const { data, isLoading, error } = useQuery<TaxSummary>({
    queryKey: ['reports', 'tax', year, activeAccountIds, filterParams],
    queryFn: () => reports.taxSummary(start, end, acctIds, filterParams),
    enabled: !(activeAccountIds !== null && activeAccountIds.length === 0),
  })
  const money = useMoney(data?.primary_currency ?? 'USD')

  const group = (kind: 'income' | 'expenses') => {
    const cats = (data?.categories ?? []).filter((c) => c.kind === kind)
    if (cats.length === 0) return null
    return (
      <section key={kind} className={`${CARD} mb-5`}>
        <h3 className="px-5 pt-4 pb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t(`reports.tax.${kind}`)}</h3>
        <ul className="divide-y divide-border">
          {cats.map((cat) => (
            <li key={`${kind}-${cat.category_id ?? 'none'}`}>
              <details>
                <summary className="px-5 py-2.5 flex items-center justify-between gap-3 cursor-pointer list-none">
                  <span className="text-sm font-medium text-foreground truncate">{cat.name || t('reports.cat.uncategorized')}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">{t('reports.cat.count', { n: cat.count })}</span>
                  </span>
                  <span className="text-sm font-bold tabular-nums shrink-0">{money(cat.total)}</span>
                </summary>
                <ul className="px-5 pb-3 space-y-1">
                  {cat.payees.map((p, i) => (
                    <li key={i} className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-muted-foreground truncate pl-4">{p.name ?? t('reports.tax.noPayee')}
                        <span className="ml-2 opacity-70">{t('reports.cat.count', { n: p.count })}</span>
                      </span>
                      <span className="tabular-nums">{money(p.total)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ul>
      </section>
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-2">
        <YearStepper year={year} onChange={setYear} />
        <ExportMenu
          report="tax-summary"
          withExtract
          params={{ start_date: start, end_date: end, ...(acctIds ? { account_ids: acctIds } : {}), ...filterParams }}
        />
      </div>
      <p className="text-xs text-muted-foreground mb-4">{t('reports.tax.hint')}</p>
      <ReportFiltersBar value={filters} onChange={setFilters} onClear={clearFilters} compact />
      {error ? <ErrorLine error={error} /> : isLoading ? <Skeleton className="h-64 w-full" /> : !data || data.categories.length === 0 ? (
        <div className={CARD}><Empty /></div>
      ) : (
        <>
          <div className={`${CARD} px-5 py-4 mb-5 grid grid-cols-2 gap-4`}>
            <Stat label={t('reports.tax.income')} value={money(data.income_total)} />
            <Stat label={t('reports.tax.expenses')} value={money(data.expense_total)} />
          </div>
          {group('income')}
          {group('expenses')}
        </>
      )}
    </div>
  )
}

export function ExtraReports({ tab }: { tab: ExtraReportTab }) {
  if (tab === 'categories') return <CategoriesReportView />
  if (tab === 'budget') return <BudgetReportView />
  if (tab === 'annual') return <AnnualReportView />
  return <TaxReportView />
}
