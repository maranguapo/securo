import { useTranslation } from 'react-i18next'
import { Send } from 'lucide-react'

import { relativeTime } from '@/lib/ai-categorization'
import type { AiSummary } from '@/types'

interface StatusStripProps {
  summary: AiSummary
  now: Date
  locale: string
}

/**
 * One line saying where things stand: what is waiting, when the AI last ran,
 * how much of the ledger has a category, and what Telegram would still send.
 */
export function StatusStrip({ summary, now, locale }: StatusStripProps) {
  const { t } = useTranslation()

  let state: string
  if (!summary.enabled) state = t('categorization.strip.off')
  else if (summary.paused) state = t('categorization.strip.paused')
  else if (summary.to_review === 0) state = t('categorization.strip.allReviewed')
  else state = t('categorization.strip.toReview', { count: summary.to_review })

  const lastRun =
    summary.last_run_at === null
      ? t('categorization.strip.neverRun')
      : t('categorization.strip.lastRun', {
          when: relativeTime(summary.last_run_at, now, locale),
          count: summary.last_run_applied ?? 0,
        })

  const telegram = summary.telegram
  return (
    <div className="space-y-2" data-testid="status-strip">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
        <span className="font-medium text-foreground" data-testid="strip-state">
          {state}
        </span>
        <span>{lastRun}</span>
        <div
          className="h-1.5 w-28 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={summary.decided_percent}
          aria-label={t('categorization.strip.progressLabel')}
        >
          <div
            className="h-full rounded-full bg-primary transition-[width]"
            style={{ width: `${summary.decided_percent}%` }}
          />
        </div>
        <span className="tabular-nums" data-testid="strip-progress">
          {t('categorization.strip.progress', {
            transactions: summary.transactions.toLocaleString(locale),
            percent: summary.decided_percent,
          })}
        </span>
        {summary.awaiting_ai > 0 && (
          <span data-testid="strip-awaiting">
            {t('categorization.strip.awaiting', { count: summary.awaiting_ai })}
          </span>
        )}
      </div>
      {telegram.digest_enabled && telegram.digest_time && (
        <p
          className="flex items-center gap-1.5 text-xs text-muted-foreground"
          data-testid="strip-telegram"
        >
          <Send className="h-3 w-3 shrink-0" />
          {telegram.pending > 0
            ? t('categorization.strip.telegramPending', {
                time: telegram.digest_time,
                count: telegram.pending,
              })
            : t('categorization.strip.telegramIdle', { time: telegram.digest_time })}
        </p>
      )}
    </div>
  )
}
