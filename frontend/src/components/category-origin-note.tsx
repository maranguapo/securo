import { useTranslation } from 'react-i18next'
import type { CategoryOrigin } from '@/types'

/**
 * One line under the category field saying what chose it: a rule (by name), the
 * AI, the bank, a recurrence, or the person. Renders nothing when the origin is
 * unknown, which is the case for categories set before this was recorded.
 */
export function CategoryOriginNote({ origin }: { origin: CategoryOrigin | undefined }) {
  const { t } = useTranslation()
  if (!origin?.source) return null

  let text: string
  switch (origin.source) {
    case 'rule':
      text = origin.rule
        ? t('transactions.categoryOrigin.rule', { name: origin.rule.name })
        : t('transactions.categoryOrigin.ruleRemoved')
      break
    case 'ai':
      text = t('transactions.categoryOrigin.ai', {
        confidence: origin.ai?.confidence != null ? Math.round(origin.ai.confidence * 100) : '—',
      })
      break
    default:
      text = t(`transactions.categoryOrigin.${origin.source}`)
  }

  return (
    <p
      className="text-xs text-muted-foreground"
      title={origin.source === 'ai' ? (origin.ai?.reason ?? undefined) : undefined}
      data-testid="category-origin"
    >
      {text}
    </p>
  )
}
