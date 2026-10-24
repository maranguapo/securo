import { render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { expect, it } from 'vitest'

import { CategoryOriginNote } from '@/components/category-origin-note'
import i18n from '@/lib/i18n'
import type { CategoryOrigin } from '@/types'

const origin = (over: Partial<CategoryOrigin>): CategoryOrigin => ({
  source: null,
  rule: null,
  rule_removed: false,
  ai: null,
  ...over,
})

const show = (value: CategoryOrigin | undefined) =>
  render(
    <I18nextProvider i18n={i18n}>
      <CategoryOriginNote origin={value} />
    </I18nextProvider>,
  )

it('names the rule that chose the category', () => {
  show(origin({ source: 'rule', rule: { id: 'r1', name: 'Streaming' } }))
  expect(screen.getByTestId('category-origin')).toHaveTextContent('Set by rule “Streaming”')
})

it('says a rule did it even after the rule was deleted', () => {
  show(origin({ source: 'rule', rule_removed: true }))
  expect(screen.getByTestId('category-origin')).toHaveTextContent(
    'Set by a rule that has since been deleted',
  )
})

it('shows the AI with its confidence and keeps its reason as a tooltip', () => {
  show(origin({ source: 'ai', ai: { confidence: 0.92, status: 'applied', reason: 'Streaming service' } }))
  const note = screen.getByTestId('category-origin')
  expect(note).toHaveTextContent('Set by the AI (92% sure)')
  expect(note).toHaveAttribute('title', 'Streaming service')
})

it('does not invent a confidence the ledger does not have', () => {
  show(origin({ source: 'ai' }))
  expect(screen.getByTestId('category-origin')).toHaveTextContent('Set by the AI (—% sure)')
})

it.each([
  ['user', 'Chosen by you'],
  ['provider', 'Came with the bank transaction'],
  ['recurring', 'Copied from the recurring transaction'],
] as const)('says when it was %s', (source, text) => {
  show(origin({ source }))
  expect(screen.getByTestId('category-origin')).toHaveTextContent(text)
})

it.each([undefined, origin({ source: null })])('shows nothing when the origin is unknown', (value) => {
  const { container } = show(value)
  expect(container).toBeEmptyDOMElement()
})
