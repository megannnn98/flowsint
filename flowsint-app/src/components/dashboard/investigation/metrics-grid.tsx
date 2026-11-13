import { Network, FileText, Users, Shield } from 'lucide-react'
import { Investigation } from '@/types'
import { useMemo } from 'react'

import { t } from '@/i18n'
type CaseOverviewPageProps = {
  investigation: Investigation
}
export function MetricsGrid({ investigation }: CaseOverviewPageProps) {
  const sketchCount = investigation.sketches?.length || 0
  const analysisCount = investigation.analyses?.length || 0
  const metrics = useMemo(
    () => [
      { label: t('Sketches'), value: sketchCount, icon: Network },
      { label: t('Analyses'), value: analysisCount, icon: FileText },
      { label: t('Entities'), value: '_', icon: Users },
      { label: t('Relations'), value: '_', icon: Shield }
      // { label: "Files", value: "34", icon: Paperclip },
      // { label: "Tasks", value: "6/15", icon: CheckCircle },
    ],
    [sketchCount, analysisCount]
  )

  return (
    <div className="my-6" style={{ containerType: 'inline-size' }}>
      <div className="grid border grid-cols-2 cq-sm:grid-cols-4 gap-px bg-border rounded-lg overflow-hidden">
        {metrics.map((metric) => (
          <div
            key={metric.label}
            className="bg-background px-4 py-4 flex flex-col items-center text-center transition-colors cursor-default"
          >
            <metric.icon className="w-4 h-4 text-muted-foreground mb-2" />
            <span className="text-xl font-semibold text-foreground">{metric.value}</span>
            <span className="text-xs text-muted-foreground mt-0.5">{metric.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
