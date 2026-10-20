import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { WorkspaceAutomation } from '@/components/workspace-automation'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  getAutomation: vi.fn(),
  saveAutomation: vi.fn(),
  runAutomation: vi.fn(),
  listConnections: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  workspaces: {
    getAutomation: api.getAutomation,
    saveAutomation: api.saveAutomation,
    runAutomation: api.runAutomation,
  },
  agents: { connections: { list: api.listConnections } },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const OFF = {
  ai_categorization_enabled: false,
  llm_connection_id: null,
  llm_model: null,
  min_confidence: 0.6,
  agents_enabled: true,
}
const ON = { ...OFF, ai_categorization_enabled: true, llm_connection_id: 'c1', llm_model: 'llama3' }
const CONNECTIONS = [
  { id: 'c1', name: 'Local', kind: 'ollama', default_model: 'llama3' },
  { id: 'c2', name: 'Cloud', kind: 'anthropic', default_model: null },
]

beforeEach(() => {
  vi.clearAllMocks()
  api.getAutomation.mockResolvedValue(OFF)
  api.listConnections.mockResolvedValue(CONNECTIONS)
})

describe('WorkspaceAutomation', () => {
  it('explains why it is unavailable when the agents module is off', async () => {
    api.getAutomation.mockResolvedValue({ ...OFF, agents_enabled: false })
    renderWithProviders(<WorkspaceAutomation workspaceId="ws-1" canManage />)

    expect(await screen.findByText(/AGENTS_ENABLED=true/)).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(api.listConnections).not.toHaveBeenCalled()
  })

  it('shows members who cannot manage the workspace no controls', async () => {
    renderWithProviders(<WorkspaceAutomation workspaceId="ws-1" canManage={false} />)

    expect(
      await screen.findByText('Only workspace owners can change automation.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(api.listConnections).not.toHaveBeenCalled()
  })

  it('will not save "on" until a connection with a model is chosen', async () => {
    const { user } = renderWithProviders(<WorkspaceAutomation workspaceId="ws-1" canManage />)
    await screen.findByText('Off')

    await user.click(screen.getByRole('switch'))
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()

    // A connection without a default model still needs one typed in.
    await user.selectOptions(screen.getByLabelText('LLM connection'), 'c2')
    expect(save).toBeDisabled()
    await user.type(screen.getByLabelText('Model'), 'claude-x')
    expect(save).toBeEnabled()
  })

  it('saves the chosen connection, model and confidence', async () => {
    api.saveAutomation.mockResolvedValue(ON)
    const { user } = renderWithProviders(<WorkspaceAutomation workspaceId="ws-1" canManage />)
    await screen.findByText('Off')
    await screen.findByRole('option', { name: 'Local (ollama)' })

    await user.click(screen.getByRole('switch'))
    await user.selectOptions(screen.getByLabelText('LLM connection'), 'c1')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.saveAutomation).toHaveBeenCalledWith('ws-1', {
        ai_categorization_enabled: true,
        llm_connection_id: 'c1',
        llm_model: null,
        min_confidence: 0.6,
      }),
    )
  })

  it('only offers "Run now" once it is saved as on, and reports the result', async () => {
    api.getAutomation.mockResolvedValue(ON)
    api.runAutomation.mockResolvedValue({ categorized: 3 })
    const { user } = renderWithProviders(<WorkspaceAutomation workspaceId="ws-1" canManage />)

    expect(await screen.findByText('On')).toBeInTheDocument()
    const run = screen.getByRole('button', { name: 'Run now' })
    expect(run).toBeEnabled()

    await user.click(run)
    await waitFor(() => expect(api.runAutomation).toHaveBeenCalledWith('ws-1'))
  })

  it('keeps "Run now" disabled while the feature is saved as off', async () => {
    renderWithProviders(<WorkspaceAutomation workspaceId="ws-1" canManage />)
    await screen.findByText('Off')

    expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled()
  })
})
