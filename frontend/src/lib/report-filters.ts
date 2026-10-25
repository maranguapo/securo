import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

export type ReportKind = '' | 'recurring' | 'installment' | 'one_off'

export interface ReportFilterValues {
  categoryIds: string[]
  uncategorized: boolean
  excludeCategories: boolean
  payeeIds: string[]
  kind: ReportKind
  minAmount: string
  maxAmount: string
}

export const EMPTY_REPORT_FILTERS: ReportFilterValues = {
  categoryIds: [],
  uncategorized: false,
  excludeCategories: false,
  payeeIds: [],
  kind: '',
  minAmount: '',
  maxAmount: '',
}

const KINDS: ReportKind[] = ['recurring', 'installment', 'one_off']

export function hasReportFilters(f: ReportFilterValues): boolean {
  return (
    f.categoryIds.length > 0 || f.uncategorized || f.payeeIds.length > 0 ||
    f.kind !== '' || f.minAmount !== '' || f.maxAmount !== ''
  )
}

/** Query params understood by the reports API. Empty filters add nothing. */
export function reportFilterParams(f: ReportFilterValues): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (f.categoryIds.length > 0) params.category_ids = f.categoryIds
  if (f.uncategorized) params.uncategorized = true
  if ((f.categoryIds.length > 0 || f.uncategorized) && f.excludeCategories) params.exclude_categories = true
  if (f.payeeIds.length > 0) params.payee_ids = f.payeeIds
  if (f.kind) params.kind = f.kind
  if (f.minAmount !== '' && Number(f.minAmount) >= 0) params.min_amount = f.minAmount
  if (f.maxAmount !== '' && Number(f.maxAmount) >= 0) params.max_amount = f.maxAmount
  return params
}

const KEYS = ['cat', 'ucat', 'xcat', 'payee', 'kind', 'min', 'max'] as const

/**
 * Report filters live in the URL, so a filtered view can be bookmarked or
 * sent as a link. Other params (tab, range) are left alone.
 */
export function useReportFilters(): [ReportFilterValues, (next: ReportFilterValues) => void, () => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.toString()

  const values = useMemo<ReportFilterValues>(() => {
    const p = new URLSearchParams(raw)
    const kind = p.get('kind') as ReportKind
    return {
      categoryIds: p.getAll('cat'),
      uncategorized: p.get('ucat') === '1',
      excludeCategories: p.get('xcat') === '1',
      payeeIds: p.getAll('payee'),
      kind: KINDS.includes(kind) ? kind : '',
      minAmount: p.get('min') ?? '',
      maxAmount: p.get('max') ?? '',
    }
  }, [raw])

  const set = useCallback((next: ReportFilterValues) => {
    setParams((current) => {
      const p = new URLSearchParams(current)
      KEYS.forEach((k) => p.delete(k))
      next.categoryIds.forEach((id) => p.append('cat', id))
      if (next.uncategorized) p.set('ucat', '1')
      if (next.excludeCategories) p.set('xcat', '1')
      next.payeeIds.forEach((id) => p.append('payee', id))
      if (next.kind) p.set('kind', next.kind)
      if (next.minAmount !== '') p.set('min', next.minAmount)
      if (next.maxAmount !== '') p.set('max', next.maxAmount)
      return p
    }, { replace: true })
  }, [setParams])

  const clear = useCallback(() => set(EMPTY_REPORT_FILTERS), [set])
  return [values, set, clear]
}
