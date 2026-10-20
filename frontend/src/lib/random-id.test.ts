import { afterEach, describe, expect, it, vi } from 'vitest'

import { randomId } from '@/lib/random-id'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

afterEach(() => vi.unstubAllGlobals())

describe('randomId', () => {
  it('returns a v4 uuid where crypto.randomUUID exists', () => {
    expect(randomId()).toMatch(UUID_V4)
  })

  it('still works over plain http, where crypto.randomUUID is missing', () => {
    // Exactly the insecure-context shape: crypto is there, randomUUID is not.
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    expect(randomId()).toMatch(UUID_V4)
  })

  it('does not even need crypto', () => {
    vi.stubGlobal('crypto', undefined)
    expect(randomId()).toMatch(UUID_V4)
  })

  it('does not repeat', () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    const ids = new Set(Array.from({ length: 500 }, () => randomId()))
    expect(ids.size).toBe(500)
  })
})
