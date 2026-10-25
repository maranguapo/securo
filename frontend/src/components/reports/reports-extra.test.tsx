import { cleanup, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useSearchParams } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExportMenu } from './ExportMenu'
import { ExtraReports } from './ExtraReports'
import { useReportFilters } from '@/lib/report-filters'

const api = vi.hoisted(() => ({
  reports: {
    categories: vi.fn(),
    annualSummary: vi.fn(),
    taxSummary: vi.fn(),
    budgetVsActual: vi.fn(),
    export: vi.fn(),
  },
  categories: { list: vi.fn().mockResolvedValue([]) },
  categoryGroups: { list: vi.fn().mockResolvedValue([]) },
  payees: { list: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '••••' }) }))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

function wrap(ui: React.ReactElement, url = '/reports') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('category report', () => {
  it('lists categories with their share and a link to the matching transactions', async () => {
    api.reports.categories.mockResolvedValue({
      primary_currency: 'BRL', kind: 'expenses', start: '2026-10-01', end: '2026-10-25',
      previous_start: '2026-09-06', previous_end: '2026-09-30', months: ['2026-10'],
      total: 400, previous_total: 200,
      items: [{
        category_id: 'cat-1', name: 'Food', color: '#f00', icon: 'x', group_name: null,
        total: 300, share_pct: 75, count: 4, previous_total: 150, delta: 150, delta_pct: 100, series: [300],
      }],
    })
    wrap(<ExtraReports tab="categories" />)
    expect(await screen.findByText('Food')).toBeInTheDocument()
    const link = screen.getByRole('link')
    expect(link.getAttribute('href')).toMatch(/^\/transactions\?category_id=cat-1&from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}$/)
    expect(screen.getAllByText('+100.0%').length).toBeGreaterThan(0)
  })
})

describe('annual summary', () => {
  it('shows a row per category and the net total', async () => {
    const months = Array(12).fill(0)
    api.reports.annualSummary.mockResolvedValue({
      primary_currency: 'BRL', year: 2026,
      rows: [{ category_id: 'c', name: 'Rent', color: '#00f', kind: 'expenses', months: months.map((_, i) => (i === 0 ? 1000 : 0)), total: 1000 }],
      income_months: months, expense_months: months.map((_, i) => (i === 0 ? 1000 : 0)),
      net_months: months.map((_, i) => (i === 0 ? -1000 : 0)),
    })
    wrap(<ExtraReports tab="annual" />)
    expect(await screen.findByText('Rent')).toBeInTheDocument()
    expect(screen.getAllByText(/1,000/).length).toBeGreaterThan(0)
  })
})

describe('export menu', () => {
  it('asks the API for the file with the on-screen params and the UI language', async () => {
    api.reports.export.mockResolvedValue({ blob: new Blob(['a,b']), filename: 'x.csv' })
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    wrap(<ExportMenu report="categories" params={{ start_date: '2026-10-01', category_ids: ['c1'] }} />)
    await user.click(screen.getByRole('button'))
    await user.click(await screen.findByText(/CSV/))
    await waitFor(() => expect(api.reports.export).toHaveBeenCalled())
    const [report, format, params] = api.reports.export.mock.calls[0]
    expect(report).toBe('categories')
    expect(format).toBe('csv')
    expect(params).toMatchObject({ start_date: '2026-10-01', category_ids: ['c1'] })
  })
})

describe('report filters in the URL', () => {
  it('round-trips through the query string and leaves other params alone', async () => {
    function Probe() {
      const [filters, setFilters] = useReportFilters()
      const [params] = useSearchParams()
      return (
        <div>
          <span data-testid="cats">{filters.categoryIds.join(',')}</span>
          <span data-testid="tab">{params.get('tab')}</span>
          <button onClick={() => setFilters({ ...filters, categoryIds: ['a', 'b'], kind: 'recurring' })}>set</button>
        </div>
      )
    }
    const user = userEvent.setup()
    wrap(<Probe />, '/reports?tab=x&cat=z')
    expect(screen.getByTestId('cats').textContent).toBe('z')
    await user.click(screen.getByText('set'))
    expect(screen.getByTestId('cats').textContent).toBe('a,b')
    expect(screen.getByTestId('tab').textContent).toBe('x')
  })
})
