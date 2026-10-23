import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { formatCurrency } from '@/lib/format'
import { getAccountLabel } from '@/lib/account-utils'
import type { Account } from '@/types'

interface Props {
  accounts: Account[]
  locale: string
  mask: (value: string) => string
}

function daysUntil(dateStr: string | null): number | null {
  if (!dateStr) return null
  const due = new Date(`${dateStr}T00:00:00`)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((due.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

function usageColor(pct: number): string {
  if (pct >= 80) return 'bg-rose-500'
  if (pct >= 50) return 'bg-amber-500'
  return 'bg-emerald-500'
}

export function CardsOverview({ accounts, locale, mask }: Props) {
  const { t } = useTranslation()
  const cards = accounts
    .filter((a) => a.type === 'credit_card' && !a.is_closed)
    .sort((a, b) => {
      // Soonest due date first; cards without one go last.
      const da = daysUntil(a.next_due_date) ?? Number.POSITIVE_INFINITY
      const db = daysUntil(b.next_due_date) ?? Number.POSITIVE_INFINITY
      return da - db
    })
  if (cards.length === 0) return null

  const dueLabel = (days: number | null) => {
    if (days == null) return null
    if (days < 0) return t('dashboard.cards.overdue')
    if (days === 0) return t('dashboard.cards.dueToday')
    return t('dashboard.cards.dueIn', { days })
  }

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm px-5 py-4">
      <p className="text-xs font-semibold text-muted-foreground mb-3">{t('dashboard.cards.title')}</p>
      <ul className="space-y-4">
        {cards.map((card) => {
          // Debt is a negative balance; a credit in the card's favour owes nothing.
          const owed = Math.max(-Number(card.current_balance), 0)
          const limit = card.credit_limit
          const pct = limit && limit > 0 ? Math.min(100, (owed / limit) * 100) : null
          const due = daysUntil(card.next_due_date)
          const close = daysUntil(card.next_close_date)
          const money = (v: number) => mask(formatCurrency(v, card.currency, locale))
          return (
            <li key={card.id}>
              <div className="flex items-baseline justify-between gap-3">
                <Link to={`/accounts/${card.id}`} className="text-sm font-medium text-foreground truncate hover:underline">
                  {getAccountLabel(card)}
                </Link>
                <span className="text-sm font-bold tabular-nums text-foreground">{money(owed)}</span>
              </div>
              {pct != null && limit != null && (
                <>
                  <div
                    className="h-1.5 rounded-full bg-muted mt-1.5 overflow-hidden"
                    role="progressbar"
                    aria-label={getAccountLabel(card)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(pct)}
                  >
                    <div className={`h-full rounded-full ${usageColor(pct)}`} style={{ width: `${pct}%` }} />
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {t('dashboard.cards.usage', { pct: Math.round(pct), limit: money(limit) })}
                  </p>
                </>
              )}
              <p className="text-[11px] text-muted-foreground mt-0.5 flex flex-wrap gap-x-3">
                {dueLabel(due) && (
                  <span className={due != null && due <= 3 && owed > 0 ? 'text-amber-600 dark:text-amber-500 font-medium' : ''}>
                    {dueLabel(due)}
                  </span>
                )}
                {close != null && close >= 0 && <span>{t('dashboard.cards.closesIn', { days: close })}</span>}
              </p>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
