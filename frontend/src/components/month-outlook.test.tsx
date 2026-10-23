import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MonthOutlook } from './month-outlook'
import type { MonthOutlook as Data } from '@/types'

const base: Data = {
  primary_currency: 'BRL',
  is_current_month: true,
  safe_to_spend: {
    available_now: 3000, upcoming_income: 0, upcoming_expenses: 800, card_bills_due: 400,
    safe_to_spend: 1800, days_left: 10, daily_allowance: 180,
  },
  pace: {
    spent: 500, reference: 'budget', reference_amount: 1000, projected_month_end: 1500,
    status: 'warning', exhaustion_date: '2026-10-20', days_elapsed: 10, days_in_month: 31,
  },
}

const props = { isLoading: false, locale: 'en-US', dateLocale: 'en-US', mask: (v: string) => v }

describe('MonthOutlook', () => {
  it('renders nothing outside the current month', () => {
    const { container } = render(<MonthOutlook {...props} data={{ ...base, is_current_month: false }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the safe-to-spend total and a pace progress bar', () => {
    render(<MonthOutlook {...props} data={base} />)
    expect(screen.getByText(/1,800/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
  })

  it('flags a negative safe-to-spend amount', () => {
    const data = { ...base, safe_to_spend: { ...base.safe_to_spend!, safe_to_spend: -250, daily_allowance: null } }
    const { container } = render(<MonthOutlook {...props} data={data} />)
    expect(container.querySelector('.text-rose-500')).not.toBeNull()
  })
})
