import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { TelegramIntegration } from '@/components/telegram-integration'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  getTelegramIntegration: vi.fn(),
  saveTelegramIntegration: vi.fn(),
  removeTelegramIntegration: vi.fn(),
  createTelegramLink: vi.fn(),
  listTelegramChats: vi.fn(),
  removeTelegramChat: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ workspaces: api }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

const CONFIGURED = { configured: true, client_id: 'my_bot' }
const MINE = {
  id: 'l1',
  user_id: 'u1',
  email: null,
  telegram_username: 'me',
  is_mine: true,
  created_at: '2026-10-20T00:00:00Z',
}
const OTHER = { ...MINE, id: 'l2', user_id: 'u2', email: 'ana@example.com', telegram_username: 'ana', is_mine: false }

beforeEach(() => {
  vi.clearAllMocks()
  api.getTelegramIntegration.mockResolvedValue({ configured: false, client_id: null })
  api.listTelegramChats.mockResolvedValue([])
})

describe('TelegramIntegration', () => {
  it('shows nothing to connect until a bot exists', async () => {
    renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage />)

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect my Telegram' })).not.toBeInTheDocument()
    expect(api.listTelegramChats).not.toHaveBeenCalled()
  })

  it('saves a trimmed token only once one is typed', async () => {
    api.saveTelegramIntegration.mockResolvedValue(CONFIGURED)
    const { user } = renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage />)
    await screen.findByText('Not configured')

    const save = screen.getByRole('button', { name: 'Save token' })
    expect(save).toBeDisabled()
    await user.type(screen.getByLabelText('Bot token'), '  123:abc ')
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(api.saveTelegramIntegration).toHaveBeenCalledWith('ws-1', '123:abc'))
  })

  it('never shows the token, and clears the field after a save', async () => {
    api.getTelegramIntegration.mockResolvedValue(CONFIGURED)
    api.saveTelegramIntegration.mockResolvedValue(CONFIGURED)
    const { user } = renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage />)

    expect(await screen.findByText('@my_bot')).toBeInTheDocument()
    const field = screen.getByLabelText('Bot token')
    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveValue('')

    await user.type(field, 'new:token')
    await user.click(screen.getByRole('button', { name: 'Save token' }))
    await waitFor(() => expect(field).toHaveValue(''))
  })

  it('opens the one-time link in a new tab when asked to connect', async () => {
    api.getTelegramIntegration.mockResolvedValue(CONFIGURED)
    api.createTelegramLink.mockResolvedValue({
      url: 'https://t.me/my_bot?start=abc',
      expires_at: '2026-10-20T00:15:00Z',
    })
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const { user } = renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage={false} />)

    await user.click(await screen.findByRole('button', { name: 'Connect my Telegram' }))

    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        'https://t.me/my_bot?start=abc',
        '_blank',
        'noopener,noreferrer',
      ),
    )
    open.mockRestore()
  })

  it('stops offering to connect once your chat is connected', async () => {
    api.getTelegramIntegration.mockResolvedValue(CONFIGURED)
    api.listTelegramChats.mockResolvedValue([MINE])
    renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage={false} />)

    expect(await screen.findByText(/Your chat/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect my Telegram' })).not.toBeInTheDocument()
  })

  it('lets a member disconnect their own chat but not see anyone else’s', async () => {
    api.getTelegramIntegration.mockResolvedValue(CONFIGURED)
    api.listTelegramChats.mockResolvedValue([MINE])
    api.removeTelegramChat.mockResolvedValue(undefined)
    const { user } = renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage={false} />)

    await user.click(await screen.findByRole('button', { name: 'Disconnect' }))

    await waitFor(() => expect(api.removeTelegramChat).toHaveBeenCalledWith('ws-1', 'l1'))
    expect(screen.queryByLabelText('Bot token')).not.toBeInTheDocument()
  })

  it('shows owners everyone’s chats, by e-mail', async () => {
    api.getTelegramIntegration.mockResolvedValue(CONFIGURED)
    api.listTelegramChats.mockResolvedValue([MINE, OTHER])
    renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage />)

    expect(await screen.findByText(/ana@example.com/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Disconnect' })).toHaveLength(2)
  })

  it('asks before removing the bot', async () => {
    api.getTelegramIntegration.mockResolvedValue(CONFIGURED)
    api.removeTelegramIntegration.mockResolvedValue(undefined)
    const { user } = renderWithProviders(<TelegramIntegration workspaceId="ws-1" canManage />)

    await user.click(await screen.findByRole('button', { name: 'Remove bot' }))
    expect(api.removeTelegramIntegration).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(api.removeTelegramIntegration).toHaveBeenCalledWith('ws-1'))
  })
})
