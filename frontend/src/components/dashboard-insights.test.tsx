import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { DashboardInsights } from './dashboard-insights'

vi.mock('@/lib/api', () => ({
  dashboard: {
    monthlyTrend: vi.fn().mockResolvedValue([]),
    healthMetrics: vi.fn().mockResolvedValue({
      primary_currency: 'BRL', savings_rate_3m: 25, avg_daily_expense: 100, fixed_coverage: null,
      top3_concentration: null, net_worth_change_30d: null, net_worth_change_30d_pct: null,
      net_worth_change_12m: null, net_worth_change_12m_pct: null, card_debt_to_income: null, ai_categorized_pct: null,
    }),
    emergencyFund: vi.fn().mockResolvedValue({
      primary_currency: 'BRL', liquid_total: 6000, avg_monthly_expenses: 2000, months_covered: 3,
      fixed_monthly: 0, months_covered_fixed: null,
    }),
    installments: vi.fn().mockResolvedValue({
      primary_currency: 'BRL', remaining_total: 900, active_purchases: 2, last_month: '2027-03',
      timeline: [{ month: '2026-11', amount: 300 }],
    }),
    categoryVariance: vi.fn().mockResolvedValue({ primary_currency: 'BRL', reference_months: 3, increases: [], decreases: [] }),
    goalsPace: vi.fn().mockResolvedValue([]),
    topPayees: vi.fn().mockResolvedValue({ primary_currency: 'BRL', total_spent: 0, items: [] }),
    subscriptions: vi.fn().mockResolvedValue({ primary_currency: 'BRL', monthly_total: 0, yearly_total: 0, items: [] }),
    investments: vi.fn().mockResolvedValue({ primary_currency: 'BRL', total_value: 0, total_cost: 0, unrealized_gain: 0, unrealized_pct: null, realized_gain: 0, allocation: [] }),
    anomalies: vi.fn().mockResolvedValue([]),
    spendingByWeekday: vi.fn().mockResolvedValue({ primary_currency: 'BRL', days: 90, items: [] }),
  },
}))

describe('DashboardInsights', () => {
  it('renders the cards that have data and hides the empty ones', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <DashboardInsights month="2026-10-01" locale="en-US" dateLocale="en-US" currency="BRL" mask={(v) => v} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByText(/900/)).toBeInTheDocument())
    expect(screen.getByText(/25%/)).toBeInTheDocument()
    expect(screen.queryByText('dashboard.insights.subscriptions.title')).not.toBeInTheDocument()
  })
})
