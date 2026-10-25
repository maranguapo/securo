import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { categories as categoriesApi, categoryGroups as categoryGroupsApi, payees as payeesApi } from '@/lib/api'
import { CategoryFilterDropdown } from '@/components/category-filter-dropdown'
import { PayeeSelect } from '@/components/payee-select'
import {
  hasReportFilters,
  type ReportFilterValues,
  type ReportKind,
} from '@/lib/report-filters'

interface Props {
  value: ReportFilterValues
  onChange: (next: ReportFilterValues) => void
  onClear: () => void
  /** Hide the kind and amount controls on reports where they make no sense. */
  compact?: boolean
}

const KIND_OPTIONS: { value: ReportKind; labelKey: string }[] = [
  { value: '', labelKey: 'reports.filters.kindAll' },
  { value: 'recurring', labelKey: 'reports.filters.kindRecurring' },
  { value: 'installment', labelKey: 'reports.filters.kindInstallment' },
  { value: 'one_off', labelKey: 'reports.filters.kindOneOff' },
]

const FIELD = 'h-8 rounded-md border border-border bg-card px-2 text-sm focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]'

/** Category, payee, kind and amount filters shared by the report tabs that can use them. */
export function ReportFiltersBar({ value, onChange, onClear, compact = false }: Props) {
  const { t } = useTranslation()
  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list })
  const { data: groups = [] } = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list })
  const { data: payees = [] } = useQuery({ queryKey: ['payees'], queryFn: payeesApi.list })
  const hasCategories = value.categoryIds.length > 0 || value.uncategorized

  return (
    <div className="flex flex-wrap items-center gap-2 mb-5" role="group" aria-label={t('reports.filters.title')}>
      <CategoryFilterDropdown
        categoryIds={value.categoryIds}
        onCategoryIdsChange={(ids) => onChange({ ...value, categoryIds: ids })}
        filterUncategorized={value.uncategorized}
        onUncategorizedChange={(on) => onChange({ ...value, uncategorized: on })}
        categories={categories}
        groups={groups}
      />

      {hasCategories && (
        <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={value.excludeCategories}
            onChange={(e) => onChange({ ...value, excludeCategories: e.target.checked })}
          />
          {t('reports.filters.exclude')}
        </label>
      )}

      <PayeeSelect
        value={value.payeeIds[0] ?? ''}
        onChange={(id) => onChange({ ...value, payeeIds: id ? [id] : [] })}
        payees={payees}
        className="min-w-[10rem]"
      />

      {!compact && (
        <>
          <select
            aria-label={t('reports.filters.kind')}
            className={FIELD}
            value={value.kind}
            onChange={(e) => onChange({ ...value, kind: e.target.value as ReportKind })}
          >
            {KIND_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
            ))}
          </select>
          <input
            type="number"
            min={0}
            inputMode="decimal"
            aria-label={t('reports.filters.minAmount')}
            placeholder={t('reports.filters.minAmount')}
            className={`${FIELD} w-28`}
            value={value.minAmount}
            onChange={(e) => onChange({ ...value, minAmount: e.target.value })}
          />
          <input
            type="number"
            min={0}
            inputMode="decimal"
            aria-label={t('reports.filters.maxAmount')}
            placeholder={t('reports.filters.maxAmount')}
            className={`${FIELD} w-28`}
            value={value.maxAmount}
            onChange={(e) => onChange({ ...value, maxAmount: e.target.value })}
          />
        </>
      )}

      {hasReportFilters(value) && (
        <button
          type="button"
          onClick={onClear}
          className="inline-flex items-center gap-1 h-8 px-2 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <X size={14} />
          {t('reports.filters.clear')}
        </button>
      )}
    </div>
  )
}
