import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Wand2 } from 'lucide-react'

import { CategoryIcon } from '@/components/category-icon'
import { CategorySelect } from '@/components/category-select'
import type { MerchantGroup } from '@/lib/ai-categorization'
import type { AiCategoryRef, Category, CategoryGroup } from '@/types'

export type SelectionState = 'none' | 'some' | 'all'

interface GroupHeaderProps {
  group: MerchantGroup
  expanded: boolean
  selection: SelectionState
  canWrite: boolean
  busy: boolean
  shared: AiCategoryRef | null
  categories: Category[]
  groups: CategoryGroup[]
  onToggleExpand: () => void
  onToggleSelect: () => void
  onChangeAll: (categoryId: string) => void
  onCreateRule: () => void
}

/**
 * A merchant that appears more than once in a day: one row to read, one place
 * to change them all, and the way to turn the answer into a rule.
 */
export function GroupHeader({
  group,
  expanded,
  selection,
  canWrite,
  busy,
  shared,
  categories,
  groups,
  onToggleExpand,
  onToggleSelect,
  onChangeAll,
  onCreateRule,
}: GroupHeaderProps) {
  const { t } = useTranslation()
  const checkbox = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (checkbox.current) checkbox.current.indeterminate = selection === 'some'
  }, [selection])

  return (
    <div
      data-testid="group-header"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 bg-muted/30 px-4 py-2"
    >
      <input
        ref={checkbox}
        type="checkbox"
        aria-label={t('categorization.group.select', { name: group.merchant })}
        checked={selection === 'all'}
        disabled={!canWrite}
        onChange={onToggleSelect}
        className="h-4 w-4 accent-primary"
      />
      <button
        type="button"
        onClick={onToggleExpand}
        aria-expanded={expanded}
        data-testid="group-toggle"
        className="flex min-w-0 items-center gap-1.5 text-left"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="truncate text-sm font-semibold text-foreground">{group.merchant}</span>
        <span className="shrink-0 text-xs text-muted-foreground">
          · {t('categorization.group.count', { count: group.items.length })}
        </span>
      </button>

      <span className="text-muted-foreground">→</span>
      {shared ? (
        <span className="inline-flex min-w-0 items-center gap-1.5 text-xs text-foreground">
          <CategoryIcon icon={shared.icon} color={shared.color} size="xs" />
          <span className="truncate">
            {shared.group ? `${shared.group} › ${shared.name}` : shared.name}
          </span>
        </span>
      ) : (
        <span className="text-xs text-muted-foreground">{t('categorization.group.mixed')}</span>
      )}

      {canWrite && (
        <div className="ml-auto flex items-center gap-2">
          <div className="w-44">
            <CategorySelect
              value={shared?.id ?? ''}
              onChange={(id) => id && onChangeAll(id)}
              categories={categories}
              groups={groups}
              placeholder={t('categorization.group.changeAll')}
              disabled={busy}
              className="h-8 w-full text-xs"
            />
          </div>
          <button
            type="button"
            onClick={onCreateRule}
            disabled={busy}
            data-testid="group-rule-button"
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
            title={t('categorization.group.createRuleHint')}
          >
            <Wand2 className="h-3.5 w-3.5" />
            {t('categorization.group.createRule')}
          </button>
        </div>
      )}
    </div>
  )
}
