import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import CategorizationPage from '@/pages/categorization'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { renderWithProviders } from '@/test/utils'
import type { AiItem, AiSummary, Category } from '@/types'

const api = vi.hoisted(() => ({
  summary: vi.fn(),
  items: vi.fn(),
  approve: vi.fn(),
  setCategory: vi.fn(),
  undo: vi.fn(),
  createRule: vi.fn(),
  runAutomation: vi.fn(),
  categoriesList: vi.fn(),
  groupsList: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  aiCategorization: {
    summary: api.summary,
    items: api.items,
    approve: api.approve,
    setCategory: api.setCategory,
    undo: api.undo,
    createRule: api.createRule,
  },
  categories: { list: api.categoriesList, create: vi.fn() },
  categoryGroups: { list: api.groupsList },
  workspaces: { runAutomation: api.runAutomation },
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const DELIVERY = { id: 'c-delivery', name: 'Delivery', group: 'Food', icon: null, color: '#f59e0b' }
const MARKET = { id: 'c-market', name: 'Market', group: 'Food', icon: null, color: '#10b981' }
const INCOME = { id: 'c-income', name: 'Income', group: null, icon: null, color: '#22c55e' }

function item(over: Partial<AiItem> = {}): AiItem {
  return {
    id: 'i1',
    transaction_id: 't1',
    status: 'applied',
    confidence: 0.92,
    reason: null,
    decided_at: '2026-10-20T10:00:00Z',
    reviewed_at: null,
    pending_telegram: true,
    description: 'iFood',
    payee: null,
    merchant: 'iFood',
    merchant_key: 'ifood',
    amount: -58.29,
    currency: 'BRL',
    date: '2026-10-20',
    account_name: 'Checking',
    category: DELIVERY,
    suggested: DELIVERY,
    installment: null,
    ...over,
  }
}

function summary(over: Partial<AiSummary> = {}): AiSummary {
  return {
    enabled: true,
    paused: false,
    last_run_at: '2026-10-22T08:00:00Z',
    last_run_applied: 3,
    transactions: 3392,
    decided: 3358,
    decided_percent: 99,
    to_review: 2,
    unconfirmed: 5,
    applied_total: 7,
    awaiting_ai: 18,
    telegram: { connected: true, digest_enabled: true, digest_time: '20:00', pending: 4 },
    ...over,
  }
}

const categories = [
  { id: 'c-delivery', name: 'Delivery', icon: 'circle-help', color: '#f59e0b', group_id: null },
  { id: 'c-market', name: 'Market', icon: 'circle-help', color: '#10b981', group_id: null },
  { id: 'c-income', name: 'Income', icon: 'circle-help', color: '#22c55e', group_id: null },
] as unknown as Category[]

function Workspace({ canWrite, canManage, children }: { canWrite: boolean; canManage: boolean; children: ReactNode }) {
  return (
    <WorkspaceContext.Provider
      value={
        { current: { id: 'ws-1' }, canWrite, canManage, hasModule: () => true } as unknown as React.ContextType<
          typeof WorkspaceContext
        >
      }
    >
      {children}
    </WorkspaceContext.Provider>
  )
}

function renderPage(items: AiItem[], opts: { tab?: string; canWrite?: boolean; canManage?: boolean; sum?: AiSummary } = {}) {
  api.items.mockResolvedValue({ items, has_more: false })
  api.summary.mockResolvedValue(opts.sum ?? summary())
  return renderWithProviders(
    <Workspace canWrite={opts.canWrite ?? true} canManage={opts.canManage ?? true}>
      <CategorizationPage />
    </Workspace>,
    { route: opts.tab ? `/categorization?tab=${opts.tab}` : '/categorization' },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.categoriesList.mockResolvedValue(categories)
  api.groupsList.mockResolvedValue([])
  api.approve.mockResolvedValue({ done: 1, skipped: 0 })
  api.setCategory.mockResolvedValue({ done: 1, skipped: 0 })
  api.undo.mockResolvedValue({ done: 1, skipped: 0 })
  api.createRule.mockResolvedValue({ rule_id: 'r1', rule_name: 'iFood → Delivery', applied: 2 })
  api.runAutomation.mockResolvedValue({ categorized: 4, digest_sent: 0 })
})

// ---------------------------------------------------------------------------
describe('the status strip', () => {
  it('says where things stand', async () => {
    renderPage([item()], { tab: 'applied' })

    expect(await screen.findByTestId('strip-state')).toHaveTextContent('2 to review')
    expect(screen.getByTestId('strip-progress')).toHaveTextContent('3,392 transactions · 99% decided')
    expect(screen.getByTestId('strip-awaiting')).toHaveTextContent('18 waiting for the AI')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '99')
  })

  it('says what Telegram would still send, so answering here has a visible effect', async () => {
    renderPage([item()], { tab: 'applied' })

    expect(await screen.findByTestId('strip-telegram')).toHaveTextContent(
      'Telegram summary at 20:00: 4 still to send. Answering here takes them out.',
    )
  })

  it('says nothing about Telegram when the summary is off', async () => {
    renderPage([item()], {
      tab: 'applied',
      sum: summary({ telegram: { connected: true, digest_enabled: false, digest_time: '20:00', pending: 4 } }),
    })

    await screen.findByTestId('strip-state')
    expect(screen.queryByTestId('strip-telegram')).not.toBeInTheDocument()
  })

  it('shows "All reviewed" when nothing waits, and the pause when the provider limits requests', async () => {
    const { unmount } = renderPage([item()], { tab: 'applied', sum: summary({ to_review: 0 }) })
    expect(await screen.findByTestId('strip-state')).toHaveTextContent('All reviewed')
    unmount()

    renderPage([item()], { tab: 'applied', sum: summary({ paused: true }) })
    expect(await screen.findByTestId('strip-state')).toHaveTextContent('Paused')
  })

  it('shows the counts on the tabs', async () => {
    renderPage([item()])

    expect(await screen.findByTestId('ai-tab-review-count')).toHaveTextContent('2')
    expect(screen.getByTestId('ai-tab-applied-count')).toHaveTextContent('7')
  })
})

// ---------------------------------------------------------------------------
describe('the review tab', () => {
  const unsure = item({
    id: 'u1',
    status: 'low_confidence',
    confidence: 0.4,
    description: 'PIX RECEBIDO',
    merchant: 'PIX RECEBIDO',
    merchant_key: 'pix recebido',
    amount: 150,
    category: null,
    suggested: INCOME,
    reason: 'Incoming transfer',
  })

  it('asks the API for the review tab and lists what waits', async () => {
    renderPage([unsure])

    expect(await screen.findByText('PIX RECEBIDO')).toBeInTheDocument()
    expect(api.items).toHaveBeenCalledWith({ tab: 'review' })
    expect(screen.getByTestId('ai-reason')).toHaveTextContent('Incoming transfer')
    expect(screen.getByTestId('confidence-pill')).toHaveAttribute('data-tone', 'low')
  })

  it('accepts the models suggestion in one click', async () => {
    const { user } = renderPage([unsure])

    await user.click(await screen.findByTestId('use-suggestion'))

    await waitFor(() => expect(api.setCategory).toHaveBeenCalledWith(['u1'], 'c-income'))
  })

  it('lets the user pick a different category', async () => {
    const { user } = renderPage([unsure])
    const row = await screen.findByTestId('ai-row')

    await user.click(within(row).getByRole('button', { name: /choose a category/i }))
    await user.click(await screen.findByText('Market'))

    await waitFor(() => expect(api.setCategory).toHaveBeenCalledWith(['u1'], 'c-market'))
  })

  it('says so when nothing waits', async () => {
    renderPage([])

    expect(await screen.findByTestId('empty-state')).toHaveTextContent('Nothing waiting for you')
  })
})

// ---------------------------------------------------------------------------
describe('the applied tab', () => {
  const ifood = (id: string, date = '2026-10-20', over: Partial<AiItem> = {}) =>
    item({ id, date, reason: 'Delivery apps deserve their own tracking.', ...over })

  it('groups by day, then by merchant, and explains each decision', async () => {
    renderPage(
      [ifood('a'), ifood('b'), item({ id: 'c', merchant: 'Uber', merchant_key: 'uber', description: 'Uber', date: '2026-10-19' })],
      { tab: 'applied' },
    )

    const days = await screen.findAllByTestId('day-group')
    expect(days).toHaveLength(2)
    const header = within(days[0]).getByTestId('group-header')
    expect(header).toHaveTextContent('iFood')
    expect(header).toHaveTextContent('2 transaction(s)')
    expect(header).toHaveTextContent('Food › Delivery')
    expect(within(days[0]).getAllByTestId('ai-reason')).toHaveLength(2)
    // A merchant that appears once gets no group header.
    expect(within(days[1]).queryByTestId('group-header')).not.toBeInTheDocument()
  })

  it('shows a purchase in parcels once, with how many parcels it covers', async () => {
    renderPage(
      [item({ id: 'p', description: 'AMAZON 01/12', installment: { number: 1, total: 12, others: 11 } })],
      { tab: 'applied' },
    )

    expect(await screen.findByTestId('installment-badge')).toHaveTextContent('1/12 · +11')
  })

  it('confirms one item from its row', async () => {
    const { user } = renderPage([item({ id: 'a' })], { tab: 'applied' })

    await user.click(await screen.findByTestId('confirm-button'))

    await waitFor(() => expect(api.approve).toHaveBeenCalledWith(['a']))
  })

  it('offers no confirm button on something already answered, but still allows undo', async () => {
    renderPage([item({ id: 'a', status: 'approved' })], { tab: 'applied' })

    // "Confirmed" is also a filter chip, so look inside the row.
    const row = await screen.findByTestId('ai-row')
    expect(within(row).getByText('Confirmed')).toBeInTheDocument()
    expect(screen.queryByTestId('confirm-button')).not.toBeInTheDocument()
    expect(screen.getByTestId('undo-button')).toBeInTheDocument()
  })

  it('undoes one item from its row', async () => {
    const { user } = renderPage([item({ id: 'a' })], { tab: 'applied' })

    await user.click(await screen.findByTestId('undo-button'))

    await waitFor(() => expect(api.undo).toHaveBeenCalledWith(['a']))
  })

  it('confirms everything unanswered in a day at once', async () => {
    const { user } = renderPage(
      [item({ id: 'a' }), item({ id: 'b', merchant_key: 'x', status: 'approved' }), item({ id: 'c', merchant_key: 'y' })],
      { tab: 'applied' },
    )

    await user.click(await screen.findByTestId('day-confirm'))

    await waitFor(() => expect(api.approve).toHaveBeenCalledWith(['a', 'c']))
  })

  it('asks before undoing a whole day, then undoes it', async () => {
    const { user } = renderPage(
      [item({ id: 'a', merchant_key: 'x' }), item({ id: 'b', merchant_key: 'y', status: 'approved' })],
      { tab: 'applied' },
    )

    await user.click(await screen.findByTestId('day-undo'))
    expect(api.undo).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toHaveTextContent('Undo 2 categorization(s)?')
    await user.click(screen.getByTestId('confirm-day-undo'))

    await waitFor(() => expect(api.undo).toHaveBeenCalledWith(['a', 'b']))
  })

  it('filters by state, by confidence and by text', async () => {
    const { user } = renderPage([item()], { tab: 'applied' })
    await screen.findByTestId('ai-row')

    await user.click(screen.getByTestId('state-approved'))
    await waitFor(() => expect(api.items).toHaveBeenLastCalledWith({ tab: 'applied', status: 'approved' }))

    await user.click(screen.getByTestId('band-ge90'))
    await waitFor(() =>
      expect(api.items).toHaveBeenLastCalledWith({ tab: 'applied', status: 'approved', min_confidence: 0.9 }),
    )

    await user.click(screen.getByTestId('band-lt90'))
    await waitFor(() =>
      expect(api.items).toHaveBeenLastCalledWith({ tab: 'applied', status: 'approved', max_confidence: 0.9 }),
    )

    await user.type(screen.getByLabelText('Search merchant or description'), 'ifo')
    await waitFor(() =>
      expect(api.items).toHaveBeenLastCalledWith({
        tab: 'applied', status: 'approved', max_confidence: 0.9, q: 'ifo',
      }),
    )
  })

  it('says so when the filters match nothing', async () => {
    api.summary.mockResolvedValue(summary())
    const { user } = renderPage([item()], { tab: 'applied' })
    await screen.findByTestId('ai-row')
    api.items.mockResolvedValue({ items: [], has_more: false })

    await user.click(screen.getByTestId('state-changed'))

    expect(await screen.findByTestId('empty-state')).toHaveTextContent('No results for these filters')
  })

  it('warns when more matched than the list shows', async () => {
    api.items.mockResolvedValue({ items: [item()], has_more: true })
    api.summary.mockResolvedValue(summary())
    renderWithProviders(
      <Workspace canWrite canManage>
        <CategorizationPage />
      </Workspace>,
      { route: '/categorization?tab=applied' },
    )

    expect(await screen.findByText(/Showing the 500 most recent/)).toBeInTheDocument()
  })

  it('switches tabs from the control', async () => {
    const { user } = renderPage([item()])
    await screen.findByTestId('strip-state')

    await user.click(screen.getByTestId('ai-tab-applied'))

    await waitFor(() => expect(api.items).toHaveBeenLastCalledWith({ tab: 'applied' }))
  })
})

// ---------------------------------------------------------------------------
describe('selecting several', () => {
  const three = () => [
    item({ id: 'a', merchant_key: 'a', description: 'A' }),
    item({ id: 'b', merchant_key: 'b', description: 'B', status: 'approved' }),
    item({ id: 'c', merchant_key: 'c', description: 'C' }),
  ]

  it('shows the bar with the count, and confirms only what can be confirmed', async () => {
    const { user } = renderPage(three(), { tab: 'applied' })
    await screen.findAllByTestId('ai-row')

    await user.click(screen.getByLabelText('Select A'))
    await user.click(screen.getByLabelText('Select B'))

    expect(screen.getByTestId('selection-count')).toHaveTextContent('2 selected')
    await user.click(screen.getByTestId('bulk-confirm'))
    // B was already answered, so it is not sent.
    await waitFor(() => expect(api.approve).toHaveBeenCalledWith(['a']))
  })

  it('undoes the selection', async () => {
    const { user } = renderPage(three(), { tab: 'applied' })
    await screen.findAllByTestId('ai-row')

    await user.click(screen.getByLabelText('Select A'))
    await user.click(screen.getByLabelText('Select C'))
    await user.click(screen.getByTestId('bulk-undo'))

    await waitFor(() => expect(api.undo).toHaveBeenCalledWith(['a', 'c']))
  })

  it('changes the category of the selection', async () => {
    const { user } = renderPage(three(), { tab: 'applied' })
    await screen.findAllByTestId('ai-row')
    await user.click(screen.getByLabelText('Select A'))
    await user.click(screen.getByLabelText('Select C'))

    const bar = screen.getByTestId('selection-bar')
    await user.click(within(bar).getByRole('button', { name: /change category/i }))
    await user.click(await screen.findByText('Market'))

    await waitFor(() => expect(api.setCategory).toHaveBeenCalledWith(['a', 'c'], 'c-market'))
  })

  it('has no bar until something is selected, and clears it', async () => {
    const { user } = renderPage(three(), { tab: 'applied' })
    await screen.findAllByTestId('ai-row')
    expect(screen.queryByTestId('selection-bar')).not.toBeInTheDocument()

    await user.click(screen.getByLabelText('Select A'))
    expect(screen.getByTestId('selection-bar')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear selection' }))

    expect(screen.queryByTestId('selection-bar')).not.toBeInTheDocument()
  })

  it('selects a whole merchant from its header', async () => {
    const { user } = renderPage(
      [item({ id: 'a' }), item({ id: 'b' }), item({ id: 'c', merchant_key: 'z', description: 'Z' })],
      { tab: 'applied' },
    )
    await screen.findAllByTestId('ai-row')

    await user.click(screen.getByLabelText('Select all from iFood'))

    expect(screen.getByTestId('selection-count')).toHaveTextContent('2 selected')
  })
})

// ---------------------------------------------------------------------------
describe('merchant groups', () => {
  const group = () => [item({ id: 'a' }), item({ id: 'b' })]

  it('changes every item of a merchant at once', async () => {
    const { user } = renderPage(group(), { tab: 'applied' })
    const header = await screen.findByTestId('group-header')

    await user.click(within(header).getByRole('button', { name: /delivery|change all/i }))
    await user.click(await screen.findByText('Market'))

    await waitFor(() => expect(api.setCategory).toHaveBeenCalledWith(['a', 'b'], 'c-market'))
  })

  it('collapses and expands', async () => {
    const { user } = renderPage(group(), { tab: 'applied' })
    await screen.findByTestId('group-header')
    expect(screen.getAllByTestId('ai-row')).toHaveLength(2)

    await user.click(screen.getByTestId('group-toggle'))
    expect(screen.queryAllByTestId('ai-row')).toHaveLength(0)

    await user.click(screen.getByTestId('group-toggle'))
    expect(screen.getAllByTestId('ai-row')).toHaveLength(2)
  })

  it('starts big groups collapsed, so one merchant does not push the day off the screen', async () => {
    const many = Array.from({ length: 6 }, (_, i) => item({ id: `m${i}` }))
    renderPage(many, { tab: 'applied' })

    await screen.findByTestId('group-header')
    expect(screen.queryAllByTestId('ai-row')).toHaveLength(0)
  })

  it('turns a decision into a rule', async () => {
    const { user } = renderPage(group(), { tab: 'applied' })

    await user.click(await screen.findByTestId('group-rule-button'))
    expect(screen.getByRole('dialog')).toHaveTextContent('“iFood”')
    await user.click(screen.getByRole('button', { name: 'Create rule' }))

    await waitFor(() => expect(api.createRule).toHaveBeenCalledWith('a', 'c-delivery'))
  })

  it('shows "several categories" when the items differ', async () => {
    renderPage([item({ id: 'a' }), item({ id: 'b', category: MARKET })], { tab: 'applied' })

    expect(await screen.findByTestId('group-header')).toHaveTextContent('several categories')
  })
})

// ---------------------------------------------------------------------------
describe('the keyboard', () => {
  it('moves between rows with the arrow keys', async () => {
    const { user } = renderPage(
      [item({ id: 'a', merchant_key: 'a', description: 'A' }), item({ id: 'b', merchant_key: 'b', description: 'B' })],
      { tab: 'applied' },
    )
    const rows = await screen.findAllByTestId('ai-row')

    rows[0].focus()
    await user.keyboard('{ArrowDown}')
    expect(rows[1]).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(rows[0]).toHaveFocus()
  })

  it('confirms with C and undoes with U', async () => {
    const { user } = renderPage([item({ id: 'a' })], { tab: 'applied' })
    const row = await screen.findByTestId('ai-row')

    row.focus()
    await user.keyboard('c')
    await waitFor(() => expect(api.approve).toHaveBeenCalledWith(['a']))
    await user.keyboard('u')
    await waitFor(() => expect(api.undo).toHaveBeenCalledWith(['a']))
  })

  it('does not treat typing inside the row as a shortcut', async () => {
    const { user } = renderPage([item({ id: 'a' })], { tab: 'applied' })
    const row = await screen.findByTestId('ai-row')

    // Focus is on a control inside the row, not on the row: "c" is just a letter.
    within(row).getByRole('checkbox').focus()
    await user.keyboard('cux')

    expect(api.approve).not.toHaveBeenCalled()
    expect(api.undo).not.toHaveBeenCalled()
    expect(screen.queryByTestId('selection-bar')).not.toBeInTheDocument()
  })

  it('selects with X', async () => {
    const { user } = renderPage([item({ id: 'a' })], { tab: 'applied' })
    const row = await screen.findByTestId('ai-row')

    row.focus()
    await user.keyboard('x')

    expect(screen.getByTestId('selection-count')).toHaveTextContent('1 selected')
  })
})

// ---------------------------------------------------------------------------
describe('who can do what', () => {
  it('lets a viewer look but offers nothing to change', async () => {
    renderPage([item({ id: 'a' }), item({ id: 'b' })], { tab: 'applied', canWrite: false, canManage: false })

    await screen.findAllByTestId('ai-row')
    expect(screen.queryByTestId('confirm-button')).not.toBeInTheDocument()
    expect(screen.queryByTestId('undo-button')).not.toBeInTheDocument()
    expect(screen.queryByTestId('day-confirm')).not.toBeInTheDocument()
    expect(screen.queryByTestId('group-rule-button')).not.toBeInTheDocument()
    expect(screen.queryByTestId('run-now')).not.toBeInTheDocument()
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()
  })

  it('lets an editor change categories but not run the AI, which spends the owners quota', async () => {
    renderPage([item({ id: 'a' })], { tab: 'applied', canWrite: true, canManage: false })

    expect(await screen.findByTestId('confirm-button')).toBeInTheDocument()
    expect(screen.queryByTestId('run-now')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
describe('running the categorization', () => {
  it('runs it now and reports what it did', async () => {
    const { user } = renderPage([item()], { tab: 'applied' })

    await user.click(await screen.findByTestId('run-now'))

    // From this screen the run must not also buzz a phone with the Telegram summary.
    await waitFor(() =>
      expect(api.runAutomation).toHaveBeenCalledWith('ws-1', { sendDigest: false }),
    )
  })

  it('cannot be run while the provider is limiting requests, or while the feature is off', async () => {
    const { unmount } = renderPage([item()], { tab: 'applied', sum: summary({ paused: true }) })
    await screen.findByTestId('strip-state')
    expect(screen.getByTestId('run-now')).toBeDisabled()
    unmount()

    renderPage([item()], { tab: 'applied', sum: summary({ enabled: false }) })
    await screen.findByTestId('strip-state')
    expect(screen.getByTestId('run-now')).toBeDisabled()
  })
})

// ---------------------------------------------------------------------------
describe('when it is not set up', () => {
  it('invites the owner to turn it on', async () => {
    renderPage([], { sum: summary({ enabled: false }) })

    const off = await screen.findByTestId('off-state')
    expect(off).toHaveTextContent('AI categorization is off')
    expect(within(off).getByRole('link', { name: 'Open settings' })).toHaveAttribute(
      'href',
      '/workspace/settings',
    )
  })

  it('does not send other members to a settings page they cannot change', async () => {
    renderPage([], { sum: summary({ enabled: false }), canWrite: true, canManage: false })

    const off = await screen.findByTestId('off-state')
    expect(within(off).queryByRole('link')).not.toBeInTheDocument()
  })

  it('still shows the history when the feature was turned off after it had worked', async () => {
    renderPage([item()], { tab: 'applied', sum: summary({ enabled: false }) })

    expect(await screen.findByTestId('ai-row')).toBeInTheDocument()
    expect(screen.queryByTestId('off-state')).not.toBeInTheDocument()
  })
})

describe('when something goes wrong', () => {
  it('offers to try loading again', async () => {
    api.summary.mockResolvedValue(summary())
    api.items.mockRejectedValueOnce(new Error('boom')).mockResolvedValue({ items: [item()], has_more: false })
    const { user } = renderWithProviders(
      <Workspace canWrite canManage>
        <CategorizationPage />
      </Workspace>,
      { route: '/categorization?tab=applied' },
    )

    expect(await screen.findByTestId('items-error')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByTestId('ai-row')).toBeInTheDocument()
  })

  it('refreshes the list after an action succeeds', async () => {
    const { user } = renderPage([item({ id: 'a' })], { tab: 'applied' })
    await screen.findByTestId('ai-row')
    const before = api.items.mock.calls.length

    await user.click(screen.getByTestId('confirm-button'))

    await waitFor(() => expect(api.items.mock.calls.length).toBeGreaterThan(before))
    await waitFor(() => expect(api.summary.mock.calls.length).toBeGreaterThan(1))
  })
})
