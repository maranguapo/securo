import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { CategorySelect } from '@/components/category-select'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Category, CategoryGroup } from '@/types'

interface RuleDialogProps {
  merchant: string
  initialCategoryId: string
  categories: Category[]
  groups: CategoryGroup[]
  pending: boolean
  onConfirm: (categoryId: string) => void
  onClose: () => void
}

/**
 * Turns a decision into a rule. Render it with a `key` per merchant so the
 * chosen category starts fresh each time it opens.
 */
export function RuleDialog({
  merchant,
  initialCategoryId,
  categories,
  groups,
  pending,
  onConfirm,
  onClose,
}: RuleDialogProps) {
  const { t } = useTranslation()
  const [categoryId, setCategoryId] = useState(initialCategoryId)

  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('categorization.rule.title')}</DialogTitle>
          <DialogDescription>
            {t('categorization.rule.description', { merchant })}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">{t('categorization.rule.category')}</label>
          <CategorySelect
            value={categoryId}
            onChange={setCategoryId}
            categories={categories}
            groups={groups}
            placeholder={t('categorization.row.chooseCategory')}
            className="h-9 w-full text-sm"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => onConfirm(categoryId)} disabled={!categoryId || pending}>
            {pending ? t('common.loading') : t('categorization.rule.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
