import { useQuery } from '@tanstack/react-query'

import { aiCategorization } from '@/lib/api'

/**
 * How many AI categorizations wait for the user's choice, for the sidebar
 * badge. Shares its query with the review screen, so opening it costs nothing.
 */
export function useAiReviewCount(enabled: boolean): number {
  const { data } = useQuery({
    queryKey: ['ai-categorization', 'summary'],
    // Read lazily: a screen that never enables this query never touches the API.
    queryFn: () => aiCategorization.summary(),
    enabled,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
    retry: false,
  })
  return data?.to_review ?? 0
}
