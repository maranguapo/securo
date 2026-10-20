import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Link2, Save, Send, Trash2 } from 'lucide-react'

import { workspaces as workspacesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'

interface TelegramIntegrationProps {
  workspaceId: string
  canManage: boolean
}

// How often to look for the newly connected chat after the link is opened.
const LINK_POLL_MS = 3000

/**
 * A Telegram bot per workspace. The owner pastes the token from @BotFather;
 * each member then connects their own chat with a one-time link, so the bot
 * only ever writes to chats that asked for it. Like the Pluggy secret, the
 * token is write-only.
 */
export function TelegramIntegration({ workspaceId, canManage }: TelegramIntegrationProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const statusKey = ['workspace-integration', 'telegram', workspaceId]
  const chatsKey = ['workspace-integration', 'telegram-chats', workspaceId]

  const [token, setToken] = useState('')
  const [removeOpen, setRemoveOpen] = useState(false)
  const [waitingForChat, setWaitingForChat] = useState(false)

  const { data: status, isLoading } = useQuery({
    queryKey: statusKey,
    queryFn: () => workspacesApi.getTelegramIntegration(workspaceId),
  })
  const configured = status?.configured ?? false

  const { data: chats = [] } = useQuery({
    queryKey: chatsKey,
    queryFn: () => workspacesApi.listTelegramChats(workspaceId),
    enabled: configured,
    refetchInterval: (query) =>
      waitingForChat && !(query.state.data ?? []).some((c) => c.is_mine) ? LINK_POLL_MS : false,
  })
  const hasMyChat = chats.some((c) => c.is_mine)

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: statusKey })
    void queryClient.invalidateQueries({ queryKey: chatsKey })
  }

  const saveMutation = useMutation({
    mutationFn: () => workspacesApi.saveTelegramIntegration(workspaceId, token.trim()),
    onSuccess: () => {
      toast.success(t('workspace.telegramSaved'))
      setToken('')
      refresh()
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.telegramSaveError'))),
  })

  const removeMutation = useMutation({
    mutationFn: () => workspacesApi.removeTelegramIntegration(workspaceId),
    onSuccess: () => {
      toast.success(t('workspace.telegramRemoved'))
      setRemoveOpen(false)
      setToken('')
      refresh()
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.telegramRemoveError'))),
  })

  const linkMutation = useMutation({
    mutationFn: () => workspacesApi.createTelegramLink(workspaceId),
    onSuccess: ({ url }) => {
      window.open(url, '_blank', 'noopener,noreferrer')
      toast.info(t('workspace.telegramLinkOpened'))
      setWaitingForChat(true)
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.telegramLinkError'))),
  })

  const disconnectMutation = useMutation({
    mutationFn: (chatId: string) => workspacesApi.removeTelegramChat(workspaceId, chatId),
    onSuccess: () => {
      toast.success(t('workspace.telegramDisconnected'))
      refresh()
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.telegramDisconnectError'))),
  })

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">Telegram</h3>
          <p className="text-xs text-muted-foreground mt-0.5">{t('workspace.telegramDescription')}</p>
        </div>
        {isLoading ? (
          <Skeleton className="h-5 w-20" />
        ) : (
          <Badge variant={configured ? 'secondary' : 'outline'} className="text-[11px] shrink-0">
            {configured && status?.client_id
              ? `@${status.client_id}`
              : configured
                ? t('workspace.pluggyConfigured')
                : t('workspace.telegramNotConfigured')}
          </Badge>
        )}
      </div>

      {canManage && (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (token.trim() && !saveMutation.isPending) saveMutation.mutate()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="telegram-token">{t('workspace.telegramBotToken')}</Label>
            <Input
              id="telegram-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={configured ? '••••••••••••' : ''}
              autoComplete="new-password"
              spellCheck={false}
            />
            <p className="text-xs text-muted-foreground">{t('workspace.telegramTokenHint')}</p>
          </div>
          <div className="flex items-center justify-end gap-2">
            {configured && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="rounded-lg"
                onClick={() => setRemoveOpen(true)}
                disabled={removeMutation.isPending}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                {t('workspace.telegramRemove')}
              </Button>
            )}
            <Button
              type="submit"
              size="sm"
              className="rounded-lg"
              disabled={!token.trim() || saveMutation.isPending}
            >
              <Save className="mr-2 h-4 w-4" />
              {saveMutation.isPending ? t('common.loading') : t('workspace.telegramSave')}
            </Button>
          </div>
        </form>
      )}

      {configured && (
        <div className="space-y-3 border-t pt-3">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t('workspace.telegramChats')}
            </h4>
            {!hasMyChat && (
              <Button
                type="button"
                size="sm"
                className="rounded-lg"
                onClick={() => linkMutation.mutate()}
                disabled={linkMutation.isPending}
              >
                <Link2 className="mr-2 h-4 w-4" />
                {t('workspace.telegramConnect')}
              </Button>
            )}
          </div>
          {chats.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('workspace.telegramNoChats')}</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {chats.map((chat) => (
                <li key={chat.id} className="flex items-center justify-between gap-2 px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <Send className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="truncate text-sm">
                      {chat.is_mine ? t('workspace.telegramYourChat') : (chat.email ?? '—')}
                      {chat.telegram_username ? ` · @${chat.telegram_username}` : ''}
                    </span>
                  </div>
                  {(chat.is_mine || canManage) && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => disconnectMutation.mutate(chat.id)}
                      disabled={disconnectMutation.isPending}
                    >
                      {t('workspace.telegramDisconnect')}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <DeleteConfirmationDialog
        open={removeOpen}
        title={t('workspace.telegramRemoveTitle')}
        description={t('workspace.telegramRemoveConfirm')}
        isPending={removeMutation.isPending}
        onClose={() => setRemoveOpen(false)}
        onConfirm={() => removeMutation.mutate()}
      />
    </div>
  )
}
