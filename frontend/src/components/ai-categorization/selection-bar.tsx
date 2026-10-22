import { useTranslation } from 'react-i18next'
import { Check, RotateCcw, X } from 'lucide-react'

import { CategorySelect } from '@/components/category-select'
import type { Category, CategoryGroup } from '@/types'

interface SelectionBarProps {
  count: number
  unconfirmed: number
  undoable: number
  busy: boolean
  categories: Category[]
  groups: CategoryGroup[]
  onApprove: () => void
  onChangeCategory: (categoryId: string) => void
  onUndo: () => void
  onClear: () => void
}

/** Floats at the bottom while anything is selected, so a long list never hides it. */
export function SelectionBar({
  count,
  unconfirmed,
  undoable,
  busy,
  categories,
  groups,
  onApprove,
  onChangeCategory,
  onUndo,
  onClear,
}: SelectionBarProps) {
  const { t } = useTranslation()
  if (count === 0) return null
  return (
    <div
      role="toolbar"
      aria-label={t('categorization.selection.label')}
      data-testid="selection-bar"
      className="fixed inset-x-3 bottom-4 z-30 mx-auto flex max-w-3xl flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-2.5 shadow-lg sm:inset-x-6"
    >
      <span className="px-2 text-sm font-medium tabular-nums" data-testid="selection-count">
        {t('categorization.selection.count', { count })}
      </span>
      <button
        type="button"
        onClick={onApprove}
        disabled={busy || unconfirmed === 0}
        data-testid="bulk-confirm"
        className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        <Check className="h-3.5 w-3.5" />
        {t('categorization.selection.confirm', { count: unconfirmed })}
      </button>
      <div className="w-48">
        <CategorySelect
          value=""
          onChange={(id) => id && onChangeCategory(id)}
          categories={categories}
          groups={groups}
          placeholder={t('categorization.selection.change')}
          disabled={busy}
          className="h-8 w-full text-xs"
          contentProps={{ side: 'top' }}
        />
      </div>
      <button
        type="button"
        onClick={onUndo}
        disabled={busy || undoable === 0}
        data-testid="bulk-undo"
        className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        {t('categorization.selection.undo')}
      </button>
      <button
        type="button"
        onClick={onClear}
        aria-label={t('categorization.selection.clear')}
        title={t('categorization.selection.clear')}
        className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
