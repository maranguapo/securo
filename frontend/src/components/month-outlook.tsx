import { useTranslation } from 'react-i18next'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCurrency } from '@/lib/format'
import type { MonthOutlook as MonthOutlookData, SafeToSpend, SpendPace } from '@/types'

interface Props {
  data?: MonthOutlookData
  isLoading: boolean
  locale: string
  dateLocale: string
  mask: (value: string) => string
}

const PACE_BAR: Record<SpendPace['status'], string> = {
  on_track: 'bg-emerald-500',
  warning: 'bg-amber-500',
  over: 'bg-rose-500',
  no_reference: 'bg-muted-foreground/40',
}

function SafeToSpendBlock({ sts, currency, locale, mask }: {
  sts: SafeToSpend; currency: string; locale: string; mask: Props['mask']
}) {
  const { t } = useTranslation()
  const money = (v: number) => mask(formatCurrency(v, currency, locale))
  const negative = sts.safe_to_spend < 0
  const rows: { label: string; value: number; sign: '+' | '−' }[] = [
    { label: t('dashboard.outlook.available'), value: sts.available_now, sign: '+' },
    { label: t('dashboard.outlook.incoming'), value: sts.upcoming_income, sign: '+' },
    { label: t('dashboard.outlook.outgoing'), value: sts.upcoming_expenses, sign: '−' },
    { label: t('dashboard.outlook.cardBills'), value: sts.card_bills_due, sign: '−' },
  ]
  return (
    <div>
      <p className="text-xs font-semibold text-muted-foreground mb-1">{t('dashboard.outlook.safeTitle')}</p>
      <p className={`text-2xl font-bold tabular-nums leading-tight ${negative ? 'text-rose-500' : 'text-foreground'}`}>
        {money(sts.safe_to_spend)}
      </p>
      <p className="text-[11px] text-muted-foreground mt-0.5">
        {negative
          ? t('dashboard.outlook.safeNegative')
          : sts.daily_allowance != null
            ? t('dashboard.outlook.safeDaily', { amount: money(sts.daily_allowance) })
            : t('dashboard.outlook.safeCaption')}
      </p>
      <dl className="mt-3 space-y-1">
        {rows.filter((r) => r.value > 0 || r.label === rows[0].label).map((r) => (
          <div key={r.label} className="flex items-center justify-between text-xs">
            <dt className="text-muted-foreground">{r.label}</dt>
            <dd className="tabular-nums text-foreground">{r.sign} {money(r.value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function PaceBlock({ pace, currency, locale, dateLocale, mask }: {
  pace: SpendPace; currency: string; locale: string; dateLocale: string; mask: Props['mask']
}) {
  const { t } = useTranslation()
  const money = (v: number) => mask(formatCurrency(v, currency, locale))
  const limit = pace.reference_amount
  const pct = limit && limit > 0 ? Math.min(100, (pace.spent / limit) * 100) : 0
  const elapsedPct = (pace.days_elapsed / pace.days_in_month) * 100

  let message: string
  if (pace.status === 'no_reference' || limit == null) {
    message = t('dashboard.outlook.paceNoReference')
  } else if (pace.status === 'over') {
    message = t('dashboard.outlook.paceOver', { amount: money(pace.spent - limit) })
  } else if (pace.status === 'warning' && pace.exhaustion_date) {
    const date = new Date(`${pace.exhaustion_date}T00:00:00`).toLocaleDateString(dateLocale, { day: 'numeric', month: 'short' })
    message = t('dashboard.outlook.paceWarning', { date })
  } else {
    message = t('dashboard.outlook.paceOnTrack', { amount: money(pace.projected_month_end ?? pace.spent) })
  }

  return (
    <div>
      <p className="text-xs font-semibold text-muted-foreground mb-1">{t('dashboard.outlook.paceTitle')}</p>
      <p className="text-2xl font-bold tabular-nums leading-tight text-foreground">
        {money(pace.spent)}
        {limit != null && (
          <span className="text-sm font-medium text-muted-foreground"> / {money(limit)}</span>
        )}
      </p>
      <p className="text-[11px] text-muted-foreground mt-0.5">
        {pace.reference === 'budget'
          ? t('dashboard.outlook.paceBudget')
          : pace.reference === 'average'
            ? t('dashboard.outlook.paceAverage')
            : t('dashboard.outlook.paceSpentOnly')}
      </p>
      {limit != null && (
        <div
          className="relative h-2 rounded-full bg-muted mt-3 overflow-hidden"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
        >
          <div className={`h-full rounded-full ${PACE_BAR[pace.status]}`} style={{ width: `${pct}%` }} />
          {/* Where the month stands: spending left of this line is ahead of the calendar's pace. */}
          <div
            className="absolute top-0 bottom-0 w-px bg-foreground/60"
            style={{ left: `${elapsedPct}%` }}
            aria-hidden="true"
          />
        </div>
      )}
      <p className={`text-xs mt-2 ${
        pace.status === 'over' ? 'text-rose-500' : pace.status === 'warning' ? 'text-amber-600 dark:text-amber-500' : 'text-muted-foreground'
      }`}>
        {message}
      </p>
    </div>
  )
}

export function MonthOutlook({ data, isLoading, locale, dateLocale, mask }: Props) {
  // Both blocks describe the rest of the current month; other months say nothing.
  if (!isLoading && (!data || !data.is_current_month)) return null

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm mb-5 px-5 py-4 grid gap-5 sm:grid-cols-2">
      {isLoading || !data ? (
        <>
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </>
      ) : (
        <>
          {data.safe_to_spend && (
            <SafeToSpendBlock sts={data.safe_to_spend} currency={data.primary_currency} locale={locale} mask={mask} />
          )}
          {data.pace && (
            <div className="relative min-w-0 before:content-[''] before:hidden sm:before:block before:absolute before:-left-2.5 before:top-1.5 before:bottom-1.5 before:w-px before:bg-border">
              <PaceBlock pace={data.pace} currency={data.primary_currency} locale={locale} dateLocale={dateLocale} mask={mask} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
