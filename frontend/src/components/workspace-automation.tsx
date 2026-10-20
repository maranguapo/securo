import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Play, Save, Sparkles } from 'lucide-react'

import { agents as agentsApi, workspaces as workspacesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'

interface WorkspaceAutomationProps {
  workspaceId: string
  canManage: boolean
}

const DEFAULT_CONFIDENCE_PERCENT = 60
const DEFAULT_DIGEST_TIME = '20:00'

/**
 * Lets a workspace have an LLM fill in the categories nothing else did.
 *
 * It only ever touches transactions that are still uncategorized, after the
 * provider's category and the user's rules have had their turn. The model is
 * one of the owner's own LLM connections (managed under Agents), so the API
 * key is never entered or shown here.
 */
export function WorkspaceAutomation({ workspaceId, canManage }: WorkspaceAutomationProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const queryKey = ['workspace-automation', workspaceId]

  const [enabled, setEnabled] = useState(false)
  const [connectionId, setConnectionId] = useState('')
  const [model, setModel] = useState('')
  const [confidence, setConfidence] = useState(DEFAULT_CONFIDENCE_PERCENT)
  const [digestEnabled, setDigestEnabled] = useState(false)
  const [digestTime, setDigestTime] = useState(DEFAULT_DIGEST_TIME)

  const { data: settings, isLoading } = useQuery({
    queryKey,
    queryFn: () => workspacesApi.getAutomation(workspaceId),
  })

  const agentsEnabled = settings?.agents_enabled ?? true
  const { data: connections = [] } = useQuery({
    queryKey: ['agents', 'connections'],
    queryFn: agentsApi.connections.list,
    // Wait for the settings: until they arrive we do not yet know whether the
    // agents routes exist on this server.
    enabled: canManage && !!settings && settings.agents_enabled,
  })

  // Whether a Telegram bot exists: without one the summary has nowhere to go.
  const { data: telegram } = useQuery({
    queryKey: ['workspace-integration', 'telegram', workspaceId],
    queryFn: () => workspacesApi.getTelegramIntegration(workspaceId),
    enabled: !!settings && settings.agents_enabled,
  })

  // Seed the form from the saved settings, and again when they change.
  const [seededFor, setSeededFor] = useState<string | null>(null)
  const seedKey = settings ? `${workspaceId}:${JSON.stringify(settings)}` : null
  if (settings && seedKey !== seededFor) {
    setSeededFor(seedKey)
    setEnabled(settings.ai_categorization_enabled)
    setConnectionId(settings.llm_connection_id ?? '')
    setModel(settings.llm_model ?? '')
    setConfidence(Math.round(settings.min_confidence * 100))
    setDigestEnabled(settings.digest_enabled)
    setDigestTime(settings.digest_time || DEFAULT_DIGEST_TIME)
  }

  const selected = connections.find((c) => c.id === connectionId)

  const saveMutation = useMutation({
    mutationFn: () =>
      workspacesApi.saveAutomation(workspaceId, {
        ai_categorization_enabled: enabled,
        llm_connection_id: connectionId || null,
        llm_model: model.trim() || null,
        min_confidence: confidence / 100,
        digest_enabled: enabled && digestEnabled,
        digest_time: digestTime || DEFAULT_DIGEST_TIME,
      }),
    onSuccess: () => {
      toast.success(t('workspace.automationSaved'))
      void queryClient.invalidateQueries({ queryKey })
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.automationSaveError'))),
  })

  const runMutation = useMutation({
    mutationFn: () => workspacesApi.runAutomation(workspaceId),
    onSuccess: ({ categorized, digest_sent }) => {
      toast.success(t('workspace.automationRunDone', { count: categorized }))
      if (digest_sent > 0) toast.success(t('workspace.automationDigestSent', { count: digest_sent }))
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
    },
    onError: (e: unknown) => toast.error(extractApiError(e, t('workspace.automationRunError'))),
  })

  const canEnable = connectionId !== '' && (model.trim() !== '' || !!selected?.default_model)
  const saved = settings?.ai_categorization_enabled ?? false

  return (
    <section className="space-y-4 rounded-xl border bg-card p-6">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-base font-semibold">{t('workspace.automationTitle')}</h2>
        </div>
        {isLoading ? (
          <Skeleton className="h-5 w-16" />
        ) : (
          <Badge variant={saved ? 'secondary' : 'outline'} className="text-[11px]">
            {saved ? t('workspace.automationOn') : t('workspace.automationOff')}
          </Badge>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{t('workspace.automationDescription')}</p>

      {!agentsEnabled ? (
        <p className="text-sm text-amber-600">{t('workspace.automationNeedsAgents')}</p>
      ) : !canManage ? (
        <p className="text-sm text-muted-foreground">{t('workspace.automationOwnersOnly')}</p>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            saveMutation.mutate()
          }}
        >
          <div className="flex items-center justify-between gap-4 rounded-lg border p-4">
            <div>
              <Label htmlFor="ai-categorization-enabled">{t('workspace.aiCategorizeLabel')}</Label>
              <p className="text-xs text-muted-foreground mt-0.5">{t('workspace.aiCategorizeHint')}</p>
            </div>
            <Switch
              id="ai-categorization-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ai-connection">{t('workspace.aiConnection')}</Label>
              <select
                id="ai-connection"
                value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              >
                <option value="">{t('workspace.aiConnectionNone')}</option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.kind})
                  </option>
                ))}
              </select>
              {connections.length === 0 && (
                <p className="text-xs text-muted-foreground">{t('workspace.aiNoConnections')}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ai-model">{t('workspace.aiModel')}</Label>
              <Input
                id="ai-model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={selected?.default_model ?? ''}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ai-confidence">{t('workspace.aiMinConfidence')}</Label>
              <div className="flex items-center gap-3">
                <input
                  id="ai-confidence"
                  type="range"
                  min={30}
                  max={100}
                  step={5}
                  value={confidence}
                  onChange={(e) => setConfidence(Number(e.target.value))}
                  className="w-full"
                />
                <span className="w-12 text-right text-sm tabular-nums">{confidence}%</span>
              </div>
              <p className="text-xs text-muted-foreground">{t('workspace.aiMinConfidenceHint')}</p>
            </div>
          </div>

          <div className="space-y-3 rounded-lg border p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="ai-digest-enabled">{t('workspace.aiDigestLabel')}</Label>
                <p className="text-xs text-muted-foreground mt-0.5">{t('workspace.aiDigestHint')}</p>
              </div>
              <Switch
                id="ai-digest-enabled"
                checked={enabled && digestEnabled}
                onCheckedChange={setDigestEnabled}
                disabled={!enabled}
              />
            </div>
            {enabled && digestEnabled && (
              <div className="flex items-center gap-3">
                <Label htmlFor="ai-digest-time">{t('workspace.aiDigestTime')}</Label>
                <Input
                  id="ai-digest-time"
                  type="time"
                  value={digestTime}
                  onChange={(e) => setDigestTime(e.target.value)}
                  className="w-28"
                />
              </div>
            )}
            {enabled && digestEnabled && telegram && !telegram.configured && (
              <p className="text-xs text-amber-600">{t('workspace.aiDigestNeedsTelegram')}</p>
            )}
          </div>

          <p className="rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
            {t('workspace.aiPrivacyNote')}
          </p>

          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="rounded-lg"
              onClick={() => runMutation.mutate()}
              disabled={!saved || runMutation.isPending}
            >
              <Play className="mr-2 h-4 w-4" />
              {runMutation.isPending ? t('common.loading') : t('workspace.automationRunNow')}
            </Button>
            <Button
              type="submit"
              size="sm"
              className="rounded-lg"
              disabled={saveMutation.isPending || (enabled && !canEnable)}
            >
              <Save className="mr-2 h-4 w-4" />
              {saveMutation.isPending ? t('common.loading') : t('common.save')}
            </Button>
          </div>
        </form>
      )}
    </section>
  )
}
