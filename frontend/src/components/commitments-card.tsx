import { useTranslation } from 'react-i18next'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCurrency } from '@/lib/format'
import type { Commitments } from '@/types'

interface Props {
  data?: Commitments
  isLoading: boolean
  /** Income and expenses expected for the month, in the primary currency. */
  income: number
  expenses: number
  locale: string
  mask: (value: string) => string
}

export function CommitmentsCard({ data, isLoading, income, expenses, locale, mask }: Props) {
  const { t } = useTranslation()
  if (!isLoading && (!data || data.fixed_total <= 0)) return null

  const money = (v: number) => mask(formatCurrency(v, data?.primary_currency ?? 'USD', locale))
  const fixed = data?.fixed_total ?? 0
  // Expenses already contain the fixed part; guard against rows the summary leaves out.
  const variable = Math.max(expenses - fixed, 0)
  const total = Math.max(income, fixed + variable)
  const share = (v: number) => (total > 0 ? (v / total) * 100 : 0)
  const fixedPctOfIncome = income > 0 ? (fixed / income) * 100 : null
  const left = Math.max(income - fixed - variable, 0)

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm px-5 py-4">
      <p className="text-xs font-semibold text-muted-foreground mb-1">{t('dashboard.commitments.title')}</p>
      {isLoading || !data ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <>
          <p className="text-2xl font-bold tabular-nums leading-tight text-foreground">
            {fixedPctOfIncome != null ? `${Math.round(fixedPctOfIncome)}%` : money(fixed)}
          </p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {fixedPctOfIncome != null
              ? t('dashboard.commitments.caption', { amount: money(fixed) })
              : t('dashboard.commitments.captionNoIncome')}
          </p>

          <div className="flex h-2 rounded-full overflow-hidden bg-muted mt-3" aria-hidden="true">
            <div className="bg-primary" style={{ width: `${share(fixed)}%` }} />
            <div className="bg-primary/40" style={{ width: `${share(variable)}%` }} />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-primary" />
              {t('dashboard.commitments.fixed')} {money(fixed)}
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-primary/40" />
              {t('dashboard.commitments.variable')} {money(variable)}
            </span>
            {income > 0 && (
              <span className="inline-flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-muted-foreground/30" />
                {t('dashboard.commitments.left')} {money(left)}
              </span>
            )}
          </div>

          <ul className="mt-3 space-y-1">
            {data.items.slice(0, 5).map((item) => (
              <li key={`${item.kind}-${item.description}`} className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate text-muted-foreground">
                  {item.description}
                  {item.installment && <span className="ml-1 text-[10px]">({item.installment})</span>}
                </span>
                <span className="tabular-nums text-foreground">{money(item.amount)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
