import type { AiItem } from '@/types'

/** How sure the model was, in the three bands the screen colours. */
export type ConfidenceTone = 'high' | 'medium' | 'low' | 'none'

export const HIGH_CONFIDENCE = 0.9
export const MEDIUM_CONFIDENCE = 0.7

export function confidenceTone(confidence: number | null | undefined): ConfidenceTone {
  if (confidence == null) return 'none'
  if (confidence >= HIGH_CONFIDENCE) return 'high'
  if (confidence >= MEDIUM_CONFIDENCE) return 'medium'
  return 'low'
}

export function confidencePercent(confidence: number | null | undefined): number | null {
  return confidence == null ? null : Math.round(confidence * 100)
}

export interface DayGroup {
  /** `YYYY-MM-DD`, as the API sends it. */
  date: string
  items: AiItem[]
}

/** Items arrive newest first; keep that order and cut a new group on each date. */
export function groupByDay(items: AiItem[]): DayGroup[] {
  const groups: DayGroup[] = []
  for (const item of items) {
    const last = groups[groups.length - 1]
    if (last && last.date === item.date) last.items.push(item)
    else groups.push({ date: item.date, items: [item] })
  }
  return groups
}

export interface MerchantGroup {
  key: string
  merchant: string
  items: AiItem[]
  /** The category every item shares, or null when they differ (or have none). */
  sharedCategoryId: string | null
}

/**
 * Collect a day's items under their merchant, in order of first appearance.
 * Items that cannot be told apart from others by merchant stay together even
 * when they are only one: the caller decides whether a single is worth a header.
 */
export function groupByMerchant(items: AiItem[]): MerchantGroup[] {
  const byKey = new Map<string, MerchantGroup>()
  for (const item of items) {
    const key = item.merchant_key || item.id
    const group = byKey.get(key)
    if (group) group.items.push(item)
    else byKey.set(key, { key, merchant: item.merchant, items: [item], sharedCategoryId: null })
  }
  const groups = [...byKey.values()]
  for (const group of groups) {
    const ids = new Set(group.items.map((i) => i.category?.id ?? null))
    const only = ids.size === 1 ? [...ids][0] : null
    group.sharedCategoryId = only
  }
  return groups
}

/** What can still be confirmed: the model applied it and nobody has answered. */
export function isUnconfirmed(item: AiItem): boolean {
  return item.status === 'applied'
}

/** What can be taken back: any category the model had a hand in. */
export function isUndoable(item: AiItem): boolean {
  return item.status === 'applied' || item.status === 'approved' || item.status === 'changed'
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "14 hours ago" in the reader's language, from an ISO timestamp. */
export function relativeTime(iso: string, now: Date, locale: string): string {
  const then = new Date(iso).getTime()
  const diff = then - now.getTime()
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  const abs = Math.abs(diff)
  if (abs < MINUTE) return formatter.format(0, 'second')
  if (abs < HOUR) return formatter.format(Math.round(diff / MINUTE), 'minute')
  if (abs < DAY) return formatter.format(Math.round(diff / HOUR), 'hour')
  return formatter.format(Math.round(diff / DAY), 'day')
}

/**
 * "Today", "yesterday", or the date, for the heading of a day's group. Days are
 * compared as calendar dates (the API sends them without a time zone), so this
 * never shifts a date by the reader's offset.
 */
export function dayLabel(date: string, today: Date, locale: string): string {
  const [y, m, d] = date.split('-').map(Number)
  const day = new Date(y, m - 1, d)
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const days = Math.round((day.getTime() - start.getTime()) / DAY)
  if (days === 0 || days === -1) {
    const label = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(days, 'day')
    return label.charAt(0).toUpperCase() + label.slice(1)
  }
  return new Intl.DateTimeFormat(locale, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(day)
}
