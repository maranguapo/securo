import { useQuery } from '@tanstack/react-query'
import { useAutoUpdateCheck } from '@/hooks/use-auto-update-check'

// This fork's own releases. Pointing at the upstream project would announce
// versions that, once installed, replace this build and its changes.
const RELEASE_REPO = 'maranguapo/securo'
const LATEST_RELEASE_URL = `https://api.github.com/repos/${RELEASE_REPO}/releases/latest`

const SIX_HOURS = 1000 * 60 * 60 * 6

export type LatestRelease = {
  tagName: string
  htmlUrl: string
}

async function fetchLatestRelease(): Promise<LatestRelease | null> {
  const response = await fetch(LATEST_RELEASE_URL, {
    headers: { Accept: 'application/vnd.github+json' },
  })
  if (!response.ok) return null
  const payload = (await response.json()) as {
    tag_name?: string
    html_url?: string
  }
  if (!payload.tag_name || !payload.html_url) return null
  return { tagName: payload.tag_name, htmlUrl: payload.html_url }
}

export function useLatestRelease() {
  const { enabled } = useAutoUpdateCheck()
  return useQuery<LatestRelease | null>({
    queryKey: ['latest-release', RELEASE_REPO],
    queryFn: fetchLatestRelease,
    enabled,
    staleTime: SIX_HOURS,
    gcTime: SIX_HOURS,
    retry: 1,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}
