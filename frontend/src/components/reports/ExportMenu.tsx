import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download } from 'lucide-react'
import { toast } from 'sonner'
import { reports } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

interface Props {
  /** Which report to export: a key the API's /reports/export accepts. */
  report: string
  /** The window, accounts and filters the screen is showing. */
  params: Record<string, unknown>
  /** Also offer the full transaction extract for the same window and filters. */
  withExtract?: boolean
}

function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** CSV / PDF download of the report on screen, with the filters it is showing. */
export function ExportMenu({ report, params, withExtract = false }: Props) {
  const { t, i18n } = useTranslation()
  const [busy, setBusy] = useState(false)
  // Spreadsheets in pt/es/de/fr... read a semicolon-separated file with decimal
  // commas; the English-style comma file would land in a single column.
  const commaDecimal = !i18n.language.toLowerCase().startsWith('en')

  const run = async (name: string, format: 'csv' | 'pdf') => {
    setBusy(true)
    try {
      const { blob, filename } = await reports.export(name, format, {
        lang: i18n.language,
        delimiter: format === 'csv' && commaDecimal ? ';' : ',',
        ...params,
      })
      save(blob, filename)
    } catch (err) {
      toast.error(extractApiError(err, t('reports.export.failed')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={busy}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md border border-border bg-card text-sm font-medium hover:bg-muted transition-colors disabled:opacity-60"
        >
          <Download size={14} />
          {t('reports.export.label')}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => run(report, 'csv')}>{t('reports.export.csv')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => run(report, 'pdf')}>{t('reports.export.pdf')}</DropdownMenuItem>
        {withExtract && (
          <DropdownMenuItem onSelect={() => run('transactions', 'csv')}>{t('reports.export.extract')}</DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
