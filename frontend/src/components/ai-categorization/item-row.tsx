import { useTranslation } from 'react-i18next'
import { Check, RotateCcw, Sparkles } from 'lucide-react'

import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import { ConfidencePill } from '@/components/ai-categorization/confidence-pill'
import { isUndoable, isUnconfirmed } from '@/lib/ai-categorization'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { AiItem, Category, CategoryGroup } from '@/types'

interface ItemRowProps {
  item: AiItem
  selected: boolean
  nested?: boolean
  canWrite: boolean
  busy: boolean
  locale: string
  categories: Category[]
  groups: CategoryGroup[]
  mask: (value: string) => string
  onToggle: (id: string) => void
  onApprove: (item: AiItem) => void
  onUndo: (item: AiItem) => void
  onChangeCategory: (item: AiItem, categoryId: string) => void
}

/** Moves focus between rows with the arrow keys, wherever they sit on the page. */
function moveFocus(from: HTMLElement, direction: 1 | -1) {
  const rows = Array.from(document.querySelectorAll<HTMLElement>('[data-ai-row]'))
  const next = rows[rows.indexOf(from) + direction]
  next?.focus()
}

export function ItemRow({
  item,
  selected,
  nested = false,
  canWrite,
  busy,
  locale,
  categories,
  groups,
  mask,
  onToggle,
  onApprove,
  onUndo,
  onChangeCategory,
}: ItemRowProps) {
  const { t } = useTranslation()
  const current = item.category
  const suggestion = item.suggested
  const showSuggestion = !current && suggestion !== null

  const handleKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // Only when the row itself has focus: inside the picker, typing is typing.
    if (e.target !== e.currentTarget) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      moveFocus(e.currentTarget, 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      moveFocus(e.currentTarget, -1)
    } else if (!canWrite || busy) {
      return
    } else if ((e.key === 'c' || e.key === 'Enter') && isUnconfirmed(item)) {
      e.preventDefault()
      onApprove(item)
    } else if (e.key === 'u' && isUndoable(item)) {
      e.preventDefault()
      onUndo(item)
    } else if (e.key === 'x' || e.key === ' ') {
      e.preventDefault()
      onToggle(item.id)
    }
  }

  return (
    <div
      data-ai-row
      data-testid="ai-row"
      data-status={item.status}
      tabIndex={0}
      onKeyDown={handleKey}
      className={cn(
        'group grid items-center gap-x-3 gap-y-1 px-4 py-2.5 outline-none transition-colors',
        'grid-cols-[auto_1fr] sm:grid-cols-[auto_minmax(0,1fr)_7.5rem_13rem_6rem_7rem]',
        'hover:bg-muted/40 focus-visible:bg-muted/60 focus-visible:ring-1 focus-visible:ring-primary/40',
        selected && 'bg-primary/[0.06]',
        nested && 'pl-9',
      )}
    >
      <input
        type="checkbox"
        aria-label={t('categorization.row.select', { name: item.description })}
        checked={selected}
        disabled={!canWrite}
        onChange={() => onToggle(item.id)}
        className="h-4 w-4 accent-primary"
      />

      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-foreground" title={item.description}>
          {item.description}
        </p>
        {item.reason && (
          <p
            data-testid="ai-reason"
            className="mt-0.5 flex items-start gap-1 text-xs italic text-muted-foreground"
          >
            <Sparkles className="mt-0.5 h-3 w-3 shrink-0 text-primary/70" />
            <span className="line-clamp-2">{item.reason}</span>
          </p>
        )}
        <p className="mt-0.5 text-[11px] text-muted-foreground sm:hidden">{item.account_name}</p>
      </div>

      <div className="col-start-2 flex flex-wrap items-center gap-1.5 sm:col-start-auto sm:justify-end">
        {item.installment && (
          <span
            data-testid="installment-badge"
            className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
            title={t('categorization.row.installmentHint', { count: item.installment.others })}
          >
            {t('categorization.row.installment', {
              number: item.installment.number ?? 1,
              total: item.installment.total ?? '?',
            })}
            {item.installment.others > 0 &&
              ` · ${t('categorization.row.installmentMore', { count: item.installment.others })}`}
          </span>
        )}
        <ConfidencePill confidence={item.confidence} />
      </div>

      <div className="col-start-2 min-w-0 sm:col-start-auto">
        {showSuggestion && canWrite && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onChangeCategory(item, suggestion.id)}
            data-testid="use-suggestion"
            className="mb-1 flex max-w-full items-center gap-1.5 rounded-md border border-dashed border-primary/40 px-2 py-1 text-xs text-primary hover:bg-primary/5 disabled:opacity-50"
          >
            <CategoryIcon icon={suggestion.icon} color={suggestion.color} size="xs" />
            <span className="truncate">
              {t('categorization.row.useSuggestion', { name: suggestion.name })}
            </span>
          </button>
        )}
        {canWrite ? (
          <CategorySelect
            value={current?.id ?? ''}
            onChange={(id) => id && id !== current?.id && onChangeCategory(item, id)}
            categories={categories}
            groups={groups}
            placeholder={t('categorization.row.chooseCategory')}
            disabled={busy}
            className="h-8 w-full text-xs"
          />
        ) : (
          <span className="text-xs text-muted-foreground">{current?.name ?? '—'}</span>
        )}
      </div>

      <div className="col-start-2 flex items-center gap-1 sm:col-start-auto sm:justify-end">
        {isUnconfirmed(item) && canWrite && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onApprove(item)}
            aria-label={t('categorization.row.confirm')}
            title={t('categorization.row.confirm')}
            data-testid="confirm-button"
            className="rounded-md p-1.5 text-emerald-600 hover:bg-emerald-500/10 disabled:opacity-50"
          >
            <Check className="h-4 w-4" />
          </button>
        )}
        {item.status === 'approved' && (
          <span className="text-[11px] font-medium text-emerald-600">
            {t('categorization.row.confirmed')}
          </span>
        )}
        {item.status === 'changed' && (
          <span className="text-[11px] font-medium text-primary">
            {t('categorization.row.corrected')}
          </span>
        )}
        {isUndoable(item) && canWrite && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onUndo(item)}
            aria-label={t('categorization.row.undo')}
            title={t('categorization.row.undo')}
            data-testid="undo-button"
            className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <p
        className={cn(
          'col-start-2 text-right text-sm font-medium tabular-nums sm:col-start-auto',
          item.amount < 0 ? 'text-foreground' : 'text-emerald-600',
        )}
      >
        {mask(formatCurrency(item.amount, item.currency, locale))}
      </p>
    </div>
  )
}
