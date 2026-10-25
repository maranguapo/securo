import { describe, expect, it } from 'vitest'
import { EMPTY_REPORT_FILTERS, hasReportFilters, reportFilterParams } from './report-filters'

describe('reportFilterParams', () => {
  it('adds nothing for empty filters', () => {
    expect(reportFilterParams(EMPTY_REPORT_FILTERS)).toEqual({})
    expect(hasReportFilters(EMPTY_REPORT_FILTERS)).toBe(false)
  })

  it('maps every control to the API parameter', () => {
    const params = reportFilterParams({
      categoryIds: ['c1', 'c2'], uncategorized: true, excludeCategories: true,
      payeeIds: ['p1'], kind: 'installment', minAmount: '10', maxAmount: '200',
    })
    expect(params).toEqual({
      category_ids: ['c1', 'c2'], uncategorized: true, exclude_categories: true,
      payee_ids: ['p1'], kind: 'installment', min_amount: '10', max_amount: '200',
    })
  })

  it('ignores "exclude" when no category is selected', () => {
    expect(reportFilterParams({ ...EMPTY_REPORT_FILTERS, excludeCategories: true })).toEqual({})
  })
})
