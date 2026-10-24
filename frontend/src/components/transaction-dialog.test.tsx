import { screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { TransactionDialog } from '@/components/transaction-dialog'
import { currencies, groups, payees, transactions as transactionsApi } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { RecurringTransaction, Transaction } from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-US',
  useDisplayLocale: () => 'en-US',
}))

afterEach(() => vi.restoreAllMocks())

it.each(['America/Los_Angeles', 'Pacific/Kiritimati'])(
  'keeps a recurring calendar date unchanged in %s',
  async (timezone) => {
    const originalTimezone = process.env.TZ
    const recurringMatch: RecurringTransaction = {
      id: 'recurring-rent',
      user_id: 'synthetic-user',
      account_id: null,
      category_id: null,
      description: 'Rent',
      amount: 100,
      currency: 'USD',
      type: 'debit',
      frequency: 'monthly',
      weekend_adjustment: 'none',
      day_of_month: 8,
      start_date: '2026-01-08',
      end_date: null,
      is_active: true,
      auto_generate: false,
      next_occurrence: '2026-03-08',
      amount_primary: null,
      fx_rate_used: null,
    }
    vi.spyOn(currencies, 'list').mockResolvedValue([])
    vi.spyOn(payees, 'list').mockResolvedValue([])
    vi.spyOn(groups, 'list').mockResolvedValue([])

    try {
      process.env.TZ = timezone
      renderWithProviders(
        <TransactionDialog
          open
          onClose={vi.fn()}
          transaction={null}
          categories={[]}
          categoryGroups={[]}
          accounts={[]}
          recurringMatch={recurringMatch}
          onSave={vi.fn()}
          loading={false}
          error={null}
        />,
      )

      expect(await screen.findByText(t('transactions.recurringInfo', {
        frequency: t('recurring.monthly'),
        next: '3/8/2026',
      }))).toBeInTheDocument()
    } finally {
      if (originalTimezone === undefined) delete process.env.TZ
      else process.env.TZ = originalTimezone
    }
  },
)

const savedTransaction = (over: Partial<Transaction> = {}): Transaction => ({
  id: 'tx-1',
  user_id: 'u1',
  account_id: 'acc-1',
  category_id: 'cat-1',
  category: null,
  external_id: null,
  description: 'NETFLIX',
  original_description: null,
  amount: 39.9,
  currency: 'USD',
  date: '2026-10-01',
  type: 'debit',
  source: 'sync',
  status: 'posted',
  payee: null,
  payee_id: null,
  payee_name: null,
  notes: null,
  transfer_pair_id: null,
  amount_primary: null,
  fx_rate_used: null,
  fx_fallback: false,
  installment_number: null,
  total_installments: null,
  installment_total_amount: null,
  installment_purchase_date: null,
  installment_series_id: null,
  bill_id: null,
  effective_bill_date: null,
  is_ignored: false,
  splits: [],
  ...over,
})

const openDetail = (transaction: Transaction) => {
  vi.spyOn(currencies, 'list').mockResolvedValue([])
  vi.spyOn(payees, 'list').mockResolvedValue([])
  vi.spyOn(groups, 'list').mockResolvedValue([])
  renderWithProviders(
    <TransactionDialog
      open
      onClose={vi.fn()}
      transaction={transaction}
      categories={[]}
      categoryGroups={[]}
      accounts={[{ id: 'acc-1', name: 'Checking' }]}
      onSave={vi.fn()}
      loading={false}
      error={null}
    />,
  )
}

it('tells which rule chose the saved category', async () => {
  vi.spyOn(transactionsApi, 'categoryOrigin').mockResolvedValue({
    source: 'rule',
    rule: { id: 'r1', name: 'Streaming' },
    rule_removed: false,
    ai: null,
  })

  openDetail(savedTransaction())

  expect(await screen.findByText('Set by rule “Streaming”')).toBeInTheDocument()
})

it('does not ask where a category came from when there is none', async () => {
  const origin = vi.spyOn(transactionsApi, 'categoryOrigin')

  openDetail(savedTransaction({ category_id: null }))

  await screen.findByDisplayValue('NETFLIX')
  expect(origin).not.toHaveBeenCalled()
})
