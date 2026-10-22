import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { UpdateAvailableBanner } from '@/components/update-available-banner'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/lib/build-info', () => ({ APP_VERSION: '0.16.4' }))

const fetchMock = vi.fn()

function release(tag: string | null) {
  if (tag === null) return Promise.resolve({ ok: false, json: async () => ({}) })
  return Promise.resolve({
    ok: true,
    json: async () => ({ tag_name: tag, html_url: `https://github.com/x/releases/${tag}` }),
  })
}

beforeEach(() => {
  localStorage.clear()
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

describe('UpdateAvailableBanner', () => {
  it('asks this fork’s repository for releases, never the upstream project’s', async () => {
    fetchMock.mockReturnValue(release('v9.0.0'))
    renderWithProviders(<UpdateAvailableBanner onOpen={vi.fn()} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toBe('https://api.github.com/repos/maranguapo/securo/releases/latest')
    expect(url).not.toContain('securo-finance')
  })

  it('shows the banner when a newer release exists', async () => {
    fetchMock.mockReturnValue(release('v0.17.0'))
    renderWithProviders(<UpdateAvailableBanner onOpen={vi.fn()} />)

    expect(await screen.findByRole('button', { name: /Update Available/ })).toBeInTheDocument()
  })

  it('stays hidden when already on the latest release', async () => {
    fetchMock.mockReturnValue(release('v0.16.4'))
    renderWithProviders(<UpdateAvailableBanner onOpen={vi.fn()} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Update Available/ })).not.toBeInTheDocument()
  })

  it('stays hidden when the repository has no release yet', async () => {
    fetchMock.mockReturnValue(release(null))
    renderWithProviders(<UpdateAvailableBanner onOpen={vi.fn()} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Update Available/ })).not.toBeInTheDocument()
  })

  it('makes no request at all when the user turned update checks off', async () => {
    localStorage.setItem('updateCheckEnabled', 'false')
    renderWithProviders(<UpdateAvailableBanner onOpen={vi.fn()} />)

    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
