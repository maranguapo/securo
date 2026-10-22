import { cn } from '@/lib/utils'
import { confidencePercent, confidenceTone } from '@/lib/ai-categorization'

const TONE = {
  high: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  medium: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  low: 'bg-red-500/10 text-red-600 dark:text-red-400',
} as const

/** How sure the model was, coloured by band. Nothing when it gave no number. */
export function ConfidencePill({ confidence }: { confidence: number | null }) {
  const tone = confidenceTone(confidence)
  const percent = confidencePercent(confidence)
  if (tone === 'none' || percent === null) return null
  return (
    <span
      data-testid="confidence-pill"
      data-tone={tone}
      className={cn(
        'inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
        TONE[tone],
      )}
    >
      {percent}%
    </span>
  )
}
