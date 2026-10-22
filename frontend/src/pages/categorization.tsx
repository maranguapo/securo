import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Check, RotateCcw, Search, Settings, Sparkles } from 'lucide-react'

import {
  aiCategorization as api,
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  workspaces as workspacesApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import {
  dayLabel,
  groupByDay,
  groupByMerchant,
  isUndoable,
  isUnconfirmed,
} from '@/lib/ai-categorization'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { PageHeader } from '@/components/page-header'
import { Segmented } from '@/components/invoice-ui'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { StatusStrip } from '@/components/ai-categorization/status-strip'
import { ItemRow } from '@/components/ai-categorization/item-row'
import { GroupHeader } from '@/components/ai-categorization/group-header'
import { SelectionBar } from '@/components/ai-categorization/selection-bar'
import { RuleDialog } from '@/components/ai-categorization/rule-dialog'
import { cn } from '@/lib/utils'
import type { AiItem, AiItemsParams, AiTab } from '@/types'

type StateFilter = 'all' | 'applied' | 'approved' | 'changed'
type BandFilter = 'all' | 'lt90' | 'ge90'

/** Groups with more rows than this start collapsed, so one chatty merchant
 *  does not push the rest of the day off the screen. */
const AUTO_COLLAPSE_ABOVE = 5

function useDebouncedValue<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return debounced
}

function Chip({
  active,
  onClick,
  children,
  testId,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  testId?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-2.5 py-1 text-xs font-medium transition-colors',
        active
          ? 'border-primary/40 bg-primary/10 text-primary'
          : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

export default function CategorizationPage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { current, canWrite, canManage } = useWorkspace()
  const { mask } = usePrivacyMode()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  const tab: AiTab = searchParams.get('tab') === 'applied' ? 'applied' : 'review'
  const [stateFilter, setStateFilter] = useState<StateFilter>('all')
  const [band, setBand] = useState<BandFilter>('all')
  const [search, setSearch] = useState('')
  const debouncedSearch = useDebouncedValue(search.trim(), 250)

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [groupOpen, setGroupOpen] = useState<Record<string, boolean>>({})
  const [rule, setRule] = useState<{ item: AiItem; merchant: string } | null>(null)
  const [undoDay, setUndoDay] = useState<AiItem[] | null>(null)

  // "14 hours ago" should keep moving while the page sits open.
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000)
    return () => clearInterval(id)
  }, [])

  const params = useMemo<AiItemsParams>(() => {
    const p: AiItemsParams = { tab }
    if (tab === 'applied') {
      if (stateFilter !== 'all') p.status = stateFilter
      if (band === 'lt90') p.max_confidence = 0.9
      if (band === 'ge90') p.min_confidence = 0.9
    }
    if (debouncedSearch) p.q = debouncedSearch
    return p
  }, [tab, stateFilter, band, debouncedSearch])

  const summaryQuery = useQuery({
    queryKey: ['ai-categorization', 'summary'],
    queryFn: api.summary,
    refetchInterval: 60_000,
  })
  const itemsQuery = useQuery({
    queryKey: ['ai-categorization', 'items', params],
    queryFn: () => api.items(params),
  })
  const { data: categories = [] } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })
  const { data: groups = [] } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
  })

  const summary = summaryQuery.data
  const items = useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data])
  const selectedItems = useMemo(() => items.filter((i) => selected.has(i.id)), [items, selected])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['ai-categorization'] })
    invalidateFinancialQueries(queryClient)
  }
  const afterAction = () => {
    setSelected(new Set())
    refresh()
  }
  const onError = (e: unknown) => toast.error(extractApiError(e, t('categorization.toast.error')))

  const approve = useMutation({
    mutationFn: (ids: string[]) => api.approve(ids),
    onSuccess: (r) => {
      toast.success(t('categorization.toast.confirmed', { count: r.done }))
      if (r.skipped > 0) toast.info(t('categorization.toast.skipped', { count: r.skipped }))
      afterAction()
    },
    onError,
  })
  const setCategory = useMutation({
    mutationFn: ({ ids, categoryId }: { ids: string[]; categoryId: string }) =>
      api.setCategory(ids, categoryId),
    onSuccess: (r) => {
      toast.success(t('categorization.toast.changed', { count: r.done }))
      afterAction()
    },
    onError,
  })
  const undo = useMutation({
    mutationFn: (ids: string[]) => api.undo(ids),
    onSuccess: (r) => {
      toast.success(t('categorization.toast.undone', { count: r.done }))
      setUndoDay(null)
      afterAction()
    },
    onError,
  })
  const createRule = useMutation({
    mutationFn: ({ id, categoryId }: { id: string; categoryId: string }) =>
      api.createRule(id, categoryId),
    onSuccess: (r) => {
      toast.success(t('categorization.rule.done', { name: r.rule_name, count: r.applied }))
      setRule(null)
      afterAction()
    },
    onError,
  })
  const runNow = useMutation({
    mutationFn: () => {
      if (!current) throw new Error('No workspace')
      return workspacesApi.runAutomation(current.id)
    },
    onSuccess: ({ categorized, digest_sent }) => {
      toast.success(t('workspace.automationRunDone', { count: categorized }))
      if (digest_sent > 0) toast.success(t('workspace.automationDigestSent', { count: digest_sent }))
      refresh()
    },
    onError,
  })
  const busy =
    approve.isPending || setCategory.isPending || undo.isPending || createRule.isPending

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const toggleMany = (ids: string[]) =>
    setSelected((prev) => {
      const next = new Set(prev)
      const all = ids.every((id) => next.has(id))
      for (const id of ids) {
        if (all) next.delete(id)
        else next.add(id)
      }
      return next
    })

  const changeTab = (next: AiTab) => {
    setSelected(new Set())
    setSearchParams(next === 'review' ? {} : { tab: next }, { replace: true })
  }

  const dayGroups = useMemo(() => groupByDay(items), [items])
  const hasFilters = debouncedSearch !== '' || stateFilter !== 'all' || band !== 'all'

  const rowProps = (item: AiItem, nested: boolean) => ({
    item,
    nested,
    selected: selected.has(item.id),
    canWrite,
    busy,
    locale,
    categories,
    groups,
    mask,
    onToggle: toggle,
    onApprove: (i: AiItem) => approve.mutate([i.id]),
    onUndo: (i: AiItem) => undo.mutate([i.id]),
    onChangeCategory: (i: AiItem, categoryId: string) =>
      setCategory.mutate({ ids: [i.id], categoryId }),
  })

  const off = summary !== undefined && !summary.enabled && items.length === 0

  return (
    <div className="pb-24">
      <PageHeader
        section={t('categorization.section')}
        title={t('nav.categorization')}
        action={
          <div className="flex items-center gap-2">
            {canManage && (
              <Link
                to="/workspace/settings"
                aria-label={t('categorization.settings')}
                title={t('categorization.settings')}
                className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Settings className="h-4 w-4" />
              </Link>
            )}
            {canManage && (
              <Button
                onClick={() => runNow.mutate()}
                disabled={runNow.isPending || !summary?.enabled || summary.paused}
                title={summary?.paused ? t('categorization.strip.paused') : undefined}
                data-testid="run-now"
                className="rounded-lg"
              >
                <Sparkles className="mr-2 h-4 w-4" />
                {runNow.isPending ? t('categorization.running') : t('categorization.run')}
              </Button>
            )}
          </div>
        }
      />

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Segmented<AiTab>
          value={tab}
          onChange={changeTab}
          testIdPrefix="ai-tab"
          options={[
            { value: 'review', label: t('categorization.tabReview'), count: summary?.to_review },
            { value: 'applied', label: t('categorization.tabApplied'), count: summary?.applied_total },
          ]}
        />
        {summary && <StatusStrip summary={summary} now={now} locale={dateLocale} />}
      </div>

      {off ? (
        <div className="rounded-xl border border-border bg-card p-8 text-center" data-testid="off-state">
          <Sparkles className="mx-auto mb-3 h-8 w-8 text-primary/70" />
          <h2 className="text-base font-semibold">{t('categorization.off.title')}</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {t('categorization.off.body')}
          </p>
          {canManage && (
            <Button asChild className="mt-4 rounded-lg">
              <Link to="/workspace/settings">{t('categorization.off.cta')}</Link>
            </Button>
          )}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">
              {tab === 'review' ? t('categorization.reviewTitle') : t('categorization.appliedTitle')}
              {summary && (
                <span className="ml-1.5 font-normal text-muted-foreground">
                  · {tab === 'review' ? summary.to_review : summary.applied_total}
                </span>
              )}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {tab === 'review' ? t('categorization.reviewHint') : t('categorization.appliedHint')}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
              <div className="relative w-full sm:w-72">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('categorization.search')}
                  aria-label={t('categorization.search')}
                  className="h-8 pl-8 text-xs"
                />
              </div>
              {tab === 'applied' && (
                <>
                  <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t('categorization.filterState')}>
                    <span className="mr-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      {t('categorization.filterState')}
                    </span>
                    {(['all', 'applied', 'approved', 'changed'] as StateFilter[]).map((value) => (
                      <Chip
                        key={value}
                        active={stateFilter === value}
                        onClick={() => setStateFilter(value)}
                        testId={`state-${value}`}
                      >
                        {t(`categorization.state.${value}`)}
                      </Chip>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t('categorization.filterConfidence')}>
                    <span className="mr-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      {t('categorization.filterConfidence')}
                    </span>
                    {(['all', 'lt90', 'ge90'] as BandFilter[]).map((value) => (
                      <Chip
                        key={value}
                        active={band === value}
                        onClick={() => setBand(value)}
                        testId={`band-${value}`}
                      >
                        {t(`categorization.band.${value}`)}
                      </Chip>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>

          {itemsQuery.isLoading && (
            <div className="space-y-2 p-4" data-testid="items-loading">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          )}

          {itemsQuery.isError && (
            <div className="p-8 text-center" data-testid="items-error">
              <p className="text-sm text-muted-foreground">{t('categorization.loadError')}</p>
              <Button variant="outline" className="mt-3" onClick={() => itemsQuery.refetch()}>
                {t('categorization.retry')}
              </Button>
            </div>
          )}

          {!itemsQuery.isLoading && !itemsQuery.isError && items.length === 0 && (
            <div className="p-10 text-center" data-testid="empty-state">
              <Check className="mx-auto mb-2 h-6 w-6 text-emerald-500" />
              <p className="text-sm font-medium">
                {hasFilters
                  ? t('categorization.empty.filtered')
                  : tab === 'review'
                    ? t('categorization.empty.review')
                    : t('categorization.empty.applied')}
              </p>
              {!hasFilters && (
                <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">
                  {tab === 'review'
                    ? t('categorization.empty.reviewHint')
                    : t('categorization.empty.appliedHint')}
                </p>
              )}
            </div>
          )}

          {dayGroups.map((day) => {
            const unconfirmed = day.items.filter(isUnconfirmed)
            const undoable = day.items.filter(isUndoable)
            return (
              <section key={day.date} data-testid="day-group" aria-label={day.date}>
                <div className="flex items-center justify-between gap-2 border-y border-border bg-muted/40 px-4 py-1.5">
                  <h3 className="text-xs font-semibold text-foreground">
                    {dayLabel(day.date, now, dateLocale)}
                    <span className="ml-1.5 font-normal text-muted-foreground">
                      {day.items.length}
                    </span>
                  </h3>
                  {canWrite && (unconfirmed.length > 0 || undoable.length > 0) && (
                    <div className="flex items-center gap-1">
                      {unconfirmed.length > 0 && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => approve.mutate(unconfirmed.map((i) => i.id))}
                          data-testid="day-confirm"
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-emerald-600 hover:bg-emerald-500/10 disabled:opacity-50"
                        >
                          <Check className="h-3 w-3" />
                          {t('categorization.day.confirm', { count: unconfirmed.length })}
                        </button>
                      )}
                      {undoable.length > 0 && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setUndoDay(undoable)}
                          data-testid="day-undo"
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                        >
                          <RotateCcw className="h-3 w-3" />
                          {t('categorization.day.undo', { count: undoable.length })}
                        </button>
                      )}
                    </div>
                  )}
                </div>

                <div className="divide-y divide-border/60">
                  {groupByMerchant(day.items).map((group) => {
                    if (group.items.length === 1) {
                      return <ItemRow key={group.items[0].id} {...rowProps(group.items[0], false)} />
                    }
                    const key = `${day.date}|${group.key}`
                    const open = groupOpen[key] ?? group.items.length <= AUTO_COLLAPSE_ABOVE
                    const ids = group.items.map((i) => i.id)
                    const chosen = ids.filter((id) => selected.has(id)).length
                    const first = group.items[0]
                    const shared =
                      group.sharedCategoryId !== null
                        ? (group.items.find((i) => i.category?.id === group.sharedCategoryId)
                            ?.category ?? null)
                        : null
                    return (
                      <div key={key}>
                        <GroupHeader
                          group={group}
                          expanded={open}
                          selection={chosen === 0 ? 'none' : chosen === ids.length ? 'all' : 'some'}
                          canWrite={canWrite}
                          busy={busy}
                          shared={shared}
                          categories={categories}
                          groups={groups}
                          onToggleExpand={() => setGroupOpen((prev) => ({ ...prev, [key]: !open }))}
                          onToggleSelect={() => toggleMany(ids)}
                          onChangeAll={(categoryId) => setCategory.mutate({ ids, categoryId })}
                          onCreateRule={() => setRule({ item: first, merchant: group.merchant })}
                        />
                        {open && (
                          <div className="divide-y divide-border/60">
                            {group.items.map((item) => (
                              <ItemRow key={item.id} {...rowProps(item, true)} />
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </section>
            )
          })}

          {itemsQuery.data?.has_more && (
            <p className="border-t border-border px-4 py-3 text-center text-xs text-muted-foreground">
              {t('categorization.truncated')}
            </p>
          )}
          {items.length > 0 && canWrite && (
            <p className="hidden border-t border-border px-4 py-2 text-[11px] text-muted-foreground sm:block">
              {t('categorization.shortcuts')}
            </p>
          )}
        </div>
      )}

      <SelectionBar
        count={selectedItems.length}
        unconfirmed={selectedItems.filter(isUnconfirmed).length}
        undoable={selectedItems.filter(isUndoable).length}
        busy={busy}
        categories={categories}
        groups={groups}
        onApprove={() => approve.mutate(selectedItems.filter(isUnconfirmed).map((i) => i.id))}
        onChangeCategory={(categoryId) =>
          setCategory.mutate({ ids: selectedItems.map((i) => i.id), categoryId })
        }
        onUndo={() => undo.mutate(selectedItems.filter(isUndoable).map((i) => i.id))}
        onClear={() => setSelected(new Set())}
      />

      {rule && (
        <RuleDialog
          key={rule.item.id}
          merchant={rule.merchant}
          initialCategoryId={rule.item.category?.id ?? rule.item.suggested?.id ?? ''}
          categories={categories}
          groups={groups}
          pending={createRule.isPending}
          onConfirm={(categoryId) => createRule.mutate({ id: rule.item.id, categoryId })}
          onClose={() => setRule(null)}
        />
      )}

      <Dialog open={undoDay !== null} onOpenChange={(open) => !open && !undo.isPending && setUndoDay(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {t('categorization.day.undoTitle', { count: undoDay?.length ?? 0 })}
            </DialogTitle>
            <DialogDescription>{t('categorization.day.undoBody')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setUndoDay(null)} disabled={undo.isPending}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => undoDay && undo.mutate(undoDay.map((i) => i.id))}
              disabled={undo.isPending}
              data-testid="confirm-day-undo"
            >
              {t('categorization.day.undoConfirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
