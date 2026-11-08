import { FileText, Network, Upload, UserPlus, CheckCircle } from 'lucide-react'

import { t } from '@/i18n'
const activities = [
  {
    icon: FileText,
    text: t('New analysis added to APT-29 Campaign'),
    time: '2 hours ago'
  },
  {
    icon: Network,
    text: t('Sketch updated in Ransomware Incident'),
    time: '5 hours ago'
  },
  {
    icon: Upload,
    text: t('Evidence uploaded to Data Breach case'),
    time: 'Yesterday'
  },
  {
    icon: UserPlus,
    text: t('Sarah Kim joined Phishing Investigation'),
    time: '2 days ago'
  },
  {
    icon: CheckCircle,
    text: t('Phishing Investigation Q4 closed'),
    time: '3 days ago'
  },
  {
    icon: FileText,
    text: t('Draft analysis saved in Insider Threat'),
    time: '4 days ago'
  }
]

export function RecentActivity() {
  return (
    <div>
      <h2 className="text-sm font-medium text-foreground mb-4">{t('Recent Activity')}</h2>
      <div className="space-y-1">
        {activities.map((activity, i) => (
          <div
            key={i}
            className="flex items-start gap-3 px-3 py-2.5 rounded-md hover:bg-muted/30 transition-colors cursor-pointer"
          >
            <div className="p-1.5 rounded bg-muted/50 mt-0.5">
              <activity.icon className="w-3 h-3 text-muted-foreground" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm text-foreground leading-snug">{activity.text}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{activity.time}</p>
            </div>
          </div>
        ))}
      </div>
      <button className="w-full mt-3 py-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
        {t('View all activity')}
      </button>
    </div>
  )
}
