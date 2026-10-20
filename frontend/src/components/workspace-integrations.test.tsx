import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { WorkspaceIntegrations } from '@/components/workspace-integrations'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  getPluggyIntegration: vi.fn(),
  savePluggyIntegration: vi.fn(),
  removePluggyIntegration: vi.fn(),
  getTelegramIntegration: vi.fn(),
  listTelegramChats: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ workspaces: api }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  api.getPluggyIntegration.mockResolvedValue({ configured: false, client_id: null })
  api.getTelegramIntegration.mockResolvedValue({ configured: false, client_id: null })
  api.listTelegramChats.mockResolvedValue([])
})

describe('WorkspaceIntegrations', () => {
  it('asks an owner for the credentials when none are saved', async () => {
    renderWithProviders(<WorkspaceIntegrations workspaceId="ws-1" canManage />)

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    expect(screen.getByLabelText('Client ID')).toHaveValue('')
    expect(screen.getByLabelText('Client Secret')).toHaveValue('')
    expect(screen.queryByRole('button', { name: /remove credentials/i })).not.toBeInTheDocument()
  })

  it('only enables saving once both fields are filled, and trims what it sends', async () => {
    api.savePluggyIntegration.mockResolvedValue({ configured: true, client_id: 'abc' })
    const { user } = renderWithProviders(<WorkspaceIntegrations workspaceId="ws-1" canManage />)
    await screen.findByText('Not configured')

    const save = screen.getByRole('button', { name: 'Save credentials' })
    expect(save).toBeDisabled()

    await user.type(screen.getByLabelText('Client ID'), '  abc ')
    expect(save).toBeDisabled()
    await user.type(screen.getByLabelText('Client Secret'), ' s3cret ')
    expect(save).toBeEnabled()

    await user.click(save)

    await waitFor(() =>
      expect(api.savePluggyIntegration).toHaveBeenCalledWith('ws-1', {
        client_id: 'abc',
        client_secret: 's3cret',
      }),
    )
  })

  it('shows the saved client id but never the secret, and clears the secret field after saving', async () => {
    api.getPluggyIntegration.mockResolvedValue({ configured: true, client_id: 'saved-id' })
    api.savePluggyIntegration.mockResolvedValue({ configured: true, client_id: 'saved-id' })
    const { user } = renderWithProviders(<WorkspaceIntegrations workspaceId="ws-1" canManage />)

    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByLabelText('Client ID')).toHaveValue('saved-id')
    const secret = screen.getByLabelText('Client Secret')
    expect(secret).toHaveValue('')
    expect(secret).toHaveAttribute('type', 'password')

    await user.type(secret, 'new-secret')
    await user.click(screen.getByRole('button', { name: 'Save credentials' }))

    await waitFor(() => expect(api.savePluggyIntegration).toHaveBeenCalled())
    await waitFor(() => expect(secret).toHaveValue(''))
  })

  it('keeps the typed values when the server rejects the credentials', async () => {
    api.savePluggyIntegration.mockRejectedValue({
      response: { data: { detail: 'Pluggy rejected these credentials' } },
    })
    const { user } = renderWithProviders(<WorkspaceIntegrations workspaceId="ws-1" canManage />)
    await screen.findByText('Not configured')

    await user.type(screen.getByLabelText('Client ID'), 'abc')
    await user.type(screen.getByLabelText('Client Secret'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Save credentials' }))

    await waitFor(() => expect(api.savePluggyIntegration).toHaveBeenCalled())
    expect(screen.getByLabelText('Client ID')).toHaveValue('abc')
    expect(screen.getByLabelText('Client Secret')).toHaveValue('wrong')
  })

  it('asks for confirmation before removing saved credentials', async () => {
    api.getPluggyIntegration.mockResolvedValue({ configured: true, client_id: 'saved-id' })
    api.removePluggyIntegration.mockResolvedValue(undefined)
    const { user } = renderWithProviders(<WorkspaceIntegrations workspaceId="ws-1" canManage />)
    await screen.findByText('Connected')

    await user.click(screen.getByRole('button', { name: /remove credentials/i }))
    expect(api.removePluggyIntegration).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(api.removePluggyIntegration).toHaveBeenCalledWith('ws-1'))
  })

  it('shows members who cannot manage the workspace no form at all', async () => {
    api.getPluggyIntegration.mockResolvedValue({ configured: true, client_id: null })
    renderWithProviders(<WorkspaceIntegrations workspaceId="ws-1" canManage={false} />)

    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.queryByLabelText('Client ID')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Client Secret')).not.toBeInTheDocument()
    expect(
      screen.getByText('Only workspace owners can change integrations.'),
    ).toBeInTheDocument()
  })
})
