import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { CardsOverview } from './cards-overview'
import { CommitmentsCard } from './commitments-card'
import type { Account, Commitments } from '@/types'

const mask = (v: string) => v
const card = (over: Partial<Account>): Account => ({
  id: 'c1', name: 'Visa', display_name: null, masked_number: null, type: 'credit_card',
  current_balance: -800, currency: 'BRL', credit_limit: 1000, is_closed: false,
  next_due_date: null, next_close_date: null, ...over,
} as Account)

describe('CardsOverview', () => {
  it('renders nothing without credit cards', () => {
    const { container } = render(
      <MemoryRouter><CardsOverview accounts={[card({ type: 'checking' })]} locale="en-US" mask={mask} /></MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows limit usage as a progress bar', () => {
    render(<MemoryRouter><CardsOverview accounts={[card({})]} locale="en-US" mask={mask} /></MemoryRouter>)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '80')
  })
})

const data: Commitments = {
  primary_currency: 'BRL', recurring_total: 400, installments_total: 600, fixed_total: 1000,
  items: [{ description: 'SOFA', amount: 600, kind: 'installment', installment: '2/10' }],
}

describe('CommitmentsCard', () => {
  it('shows the share of income that is committed', () => {
    render(<CommitmentsCard data={data} isLoading={false} income={4000} expenses={2500} locale="en-US" mask={mask} />)
    expect(screen.getByText('25%')).toBeInTheDocument()
    expect(screen.getByText(/SOFA/)).toBeInTheDocument()
  })

  it('renders nothing when nothing is committed', () => {
    const { container } = render(
      <CommitmentsCard data={{ ...data, fixed_total: 0, items: [] }} isLoading={false} income={0} expenses={0} locale="en-US" mask={mask} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
