import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Plug, Save, Trash2 } from 'lucide-react'

import { workspaces as workspacesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { TelegramIntegration } from '@/components/telegram-integration'

interface WorkspaceIntegrationsProps {
  workspaceId: string
  canManage: boolean
}

/**
 * Credentials this workspace uses to reach third-party services.
 *
 * Pluggy used to be configured once for the whole server. It is per workspace
 * now, so each person on a shared install connects with their own Pluggy
 * account. The secret is write-only: after saving, only "connected" and the
 * client id are shown.
 */
export function WorkspaceIntegrations({ workspaceId, canManage }: WorkspaceIntegrationsProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const queryKey = ['workspace-integration', 'pluggy', workspaceId]

  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [removeOpen, setRemoveOpen] = useState(false)

  const { data: status, isLoading } = useQuery({
    queryKey,
    queryFn: () => workspacesApi.getPluggyIntegration(workspaceId),
  })

  // Seed the form from the saved client id once it loads, and again when the
  // workspace changes. Derived during render rather than in an effect, as the
  // workspace settings page does for its own form.
  const [seededFor, setSeededFor] = useState<string | null>(null)
  const seedKey = status ? `${workspaceId}:${status.client_id ?? ''}` : null
  if (seedKey && seedKey !== seededFor) {
    setSeededFor(seedKey)
    setClientId(status?.client_id ?? '')
    setClientSecret('')
  }

  const refreshConnectors = () => {
    // The connector picker reads `configured` from this query.
    void queryClient.invalidateQueries({ queryKey: ['connections', 'providers'] })
    void queryClient.invalidateQueries({ queryKey })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      workspacesApi.savePluggyIntegration(workspaceId, {
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
      }),
    onSuccess: () => {
      toast.success(t('workspace.pluggySaved'))
      setClientSecret('')
      refreshConnectors()
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.pluggySaveError'))),
  })

  const removeMutation = useMutation({
    mutationFn: () => workspacesApi.removePluggyIntegration(workspaceId),
    onSuccess: () => {
      toast.success(t('workspace.pluggyRemoved'))
      setRemoveOpen(false)
      setClientId('')
      setClientSecret('')
      refreshConnectors()
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.pluggyRemoveError'))),
  })

  const configured = status?.configured ?? false
  const canSave = clientId.trim() !== '' && clientSecret.trim() !== '' && !saveMutation.isPending

  return (
    <section className="space-y-4 rounded-xl border bg-card p-6">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Plug className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-base font-semibold">{t('workspace.integrationsTitle')}</h2>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{t('workspace.integrationsDescription')}</p>

      <div className="space-y-4 rounded-lg border p-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-medium">Pluggy</h3>
            <p className="text-xs text-muted-foreground mt-0.5">{t('workspace.pluggyDescription')}</p>
          </div>
          {isLoading ? (
            <Skeleton className="h-5 w-20" />
          ) : (
            <Badge variant={configured ? 'secondary' : 'outline'} className="text-[11px] shrink-0">
              {configured ? t('workspace.pluggyConfigured') : t('workspace.pluggyNotConfigured')}
            </Badge>
          )}
        </div>

        {canManage ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (canSave) saveMutation.mutate()
            }}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="pluggy-client-id">{t('workspace.pluggyClientId')}</Label>
                <Input
                  id="pluggy-client-id"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pluggy-client-secret">{t('workspace.pluggyClientSecret')}</Label>
                <Input
                  id="pluggy-client-secret"
                  type="password"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  placeholder={configured ? '••••••••••••' : ''}
                  autoComplete="new-password"
                  spellCheck={false}
                />
                <p className="text-xs text-muted-foreground">{t('workspace.pluggySecretHint')}</p>
              </div>
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
                  {t('workspace.pluggyRemove')}
                </Button>
              )}
              <Button type="submit" size="sm" className="rounded-lg" disabled={!canSave}>
                <Save className="mr-2 h-4 w-4" />
                {saveMutation.isPending ? t('common.loading') : t('workspace.pluggySave')}
              </Button>
            </div>
          </form>
        ) : (
          <p className="text-sm text-muted-foreground">{t('workspace.integrationsOwnersOnly')}</p>
        )}
      </div>

      <TelegramIntegration workspaceId={workspaceId} canManage={canManage} />

      <DeleteConfirmationDialog
        open={removeOpen}
        title={t('workspace.pluggyRemoveTitle')}
        description={t('workspace.pluggyRemoveConfirm')}
        isPending={removeMutation.isPending}
        onClose={() => setRemoveOpen(false)}
        onConfirm={() => removeMutation.mutate()}
      />
    </section>
  )
}
