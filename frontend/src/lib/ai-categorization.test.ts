import { describe, expect, it } from 'vitest'

import {
  confidencePercent,
  confidenceTone,
  dayLabel,
  groupByDay,
  groupByMerchant,
  isUndoable,
  isUnconfirmed,
  relativeTime,
} from '@/lib/ai-categorization'
import type { AiItem } from '@/types'

function item(over: Partial<AiItem> = {}): AiItem {
  return {
    id: 'i1',
    transaction_id: 't1',
    status: 'applied',
    confidence: 0.9,
    reason: null,
    decided_at: '2026-10-20T10:00:00Z',
    reviewed_at: null,
    pending_telegram: true,
    description: 'iFood',
    payee: null,
    merchant: 'iFood',
    merchant_key: 'ifood',
    amount: -10,
    currency: 'BRL',
    date: '2026-10-20',
    account_name: 'Conta',
    category: { id: 'c1', name: 'Delivery', group: 'Alimentação', icon: null, color: null },
    suggested: null,
    installment: null,
    ...over,
  }
}

describe('confidence', () => {
  it('colours three bands', () => {
    expect(confidenceTone(0.95)).toBe('high')
    expect(confidenceTone(0.9)).toBe('high')
    expect(confidenceTone(0.89)).toBe('medium')
    expect(confidenceTone(0.7)).toBe('medium')
    expect(confidenceTone(0.69)).toBe('low')
    expect(confidenceTone(null)).toBe('none')
  })

  it('shows a whole percent', () => {
    expect(confidencePercent(0.876)).toBe(88)
    expect(confidencePercent(null)).toBeNull()
  })
})

describe('groupByDay', () => {
  it('cuts a group each time the date changes and keeps the order', () => {
    const groups = groupByDay([
      item({ id: 'a', date: '2026-10-21' }),
      item({ id: 'b', date: '2026-10-20' }),
      item({ id: 'c', date: '2026-10-20' }),
    ])

    expect(groups.map((g) => [g.date, g.items.map((i) => i.id)])).toEqual([
      ['2026-10-21', ['a']],
      ['2026-10-20', ['b', 'c']],
    ])
  })

  it('is empty for no items', () => {
    expect(groupByDay([])).toEqual([])
  })
})

describe('groupByMerchant', () => {
  it('collects items under their merchant in order of first appearance', () => {
    const groups = groupByMerchant([
      item({ id: 'a', merchant_key: 'ifood', merchant: 'iFood' }),
      item({ id: 'b', merchant_key: 'uber', merchant: 'Uber' }),
      item({ id: 'c', merchant_key: 'ifood', merchant: 'iFood' }),
    ])

    expect(groups.map((g) => [g.key, g.items.map((i) => i.id)])).toEqual([
      ['ifood', ['a', 'c']],
      ['uber', ['b']],
    ])
  })

  it('reports the category a group shares, and none when they differ', () => {
    const delivery = { id: 'c1', name: 'Delivery', group: null, icon: null, color: null }
    const food = { id: 'c2', name: 'Mercado', group: null, icon: null, color: null }

    const same = groupByMerchant([item({ category: delivery }), item({ id: 'b', category: delivery })])
    const mixed = groupByMerchant([item({ category: delivery }), item({ id: 'b', category: food })])
    const none = groupByMerchant([item({ category: null }), item({ id: 'b', category: null })])

    expect(same[0].sharedCategoryId).toBe('c1')
    expect(mixed[0].sharedCategoryId).toBeNull()
    expect(none[0].sharedCategoryId).toBeNull()
  })

  it('never merges items that have no merchant key', () => {
    const groups = groupByMerchant([
      item({ id: 'a', merchant_key: '' }),
      item({ id: 'b', merchant_key: '' }),
    ])
    expect(groups).toHaveLength(2)
  })
})

describe('what an item allows', () => {
  it('can only be confirmed while the model has applied it and nobody answered', () => {
    expect(isUnconfirmed(item({ status: 'applied' }))).toBe(true)
    expect(isUnconfirmed(item({ status: 'approved' }))).toBe(false)
    expect(isUnconfirmed(item({ status: 'low_confidence' }))).toBe(false)
  })

  it('can be taken back once the model put a category on it', () => {
    expect(isUndoable(item({ status: 'applied' }))).toBe(true)
    expect(isUndoable(item({ status: 'approved' }))).toBe(true)
    expect(isUndoable(item({ status: 'changed' }))).toBe(true)
    expect(isUndoable(item({ status: 'low_confidence' }))).toBe(false)
  })
})

describe('relativeTime', () => {
  const now = new Date('2026-10-22T12:00:00Z')

  it('speaks in the reader’s language and unit', () => {
    expect(relativeTime('2026-10-21T22:00:00Z', now, 'en')).toBe('14 hours ago')
    expect(relativeTime('2026-10-21T22:00:00Z', now, 'pt-BR')).toBe('há 14 horas')
    expect(relativeTime('2026-10-22T11:30:00Z', now, 'en')).toBe('30 minutes ago')
    expect(relativeTime('2026-10-19T12:00:00Z', now, 'en')).toBe('3 days ago')
    expect(relativeTime('2026-10-22T11:59:40Z', now, 'en')).toBe('now')
  })
})

describe('dayLabel', () => {
  const today = new Date(2026, 9, 22, 15, 0)

  it('names today and yesterday', () => {
    expect(dayLabel('2026-10-22', today, 'en')).toBe('Today')
    expect(dayLabel('2026-10-21', today, 'en')).toBe('Yesterday')
    expect(dayLabel('2026-10-22', today, 'pt-BR')).toBe('Hoje')
    expect(dayLabel('2026-10-21', today, 'pt-BR')).toBe('Ontem')
  })

  it('writes any other day out, without shifting it by the reader’s offset', () => {
    expect(dayLabel('2026-10-20', today, 'en')).toContain('20')
    expect(dayLabel('2026-10-20', today, 'en')).toContain('2026')
    expect(dayLabel('2026-10-01', today, 'en')).toContain('1')
  })
})
