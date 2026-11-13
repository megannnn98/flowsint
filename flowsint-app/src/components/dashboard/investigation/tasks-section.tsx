import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useState } from 'react'

import { t } from '@/i18n'
type Task = {
  id: number
  title: string
  assignee: string
  dueDate: string
  completed: boolean
}

const tasksData = [
  {
    id: 1,
    title: t('Complete malware reverse engineering'),
    assignee: 'SK',
    dueDate: 'Dec 1',
    completed: false
  },
  {
    id: 2,
    title: t('Document C2 infrastructure'),
    assignee: 'JD',
    dueDate: 'Dec 2',
    completed: false
  },
  {
    id: 3,
    title: t('Correlate IOCs with threat intel'),
    assignee: 'MR',
    dueDate: 'Dec 3',
    completed: false
  },
  { id: 4, title: t('Initial triage review'), assignee: 'SK', dueDate: 'Nov 26', completed: true },
  {
    id: 5,
    title: t('Collect network artifacts'),
    assignee: 'MR',
    dueDate: 'Nov 25',
    completed: true
  }
]

export function TasksSection() {
  const [tasks, setTasks] = useState<Task[]>(tasksData)
  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium text-foreground">{t('Tasks')}</h3>
          <span className="text-xs text-muted-foreground">2/5</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs text-muted-foreground hover:text-foreground gap-1"
        >
          <Plus className="w-3.5 h-3.5" />
          {t('Add')}
        </Button>
      </div>

      <div className="space-y-1">
        {tasks.map((task) => (
          <div
            key={task.id}
            className={cn('flex items-center gap-3 py-1.5', task.completed && 'opacity-50')}
          >
            <Checkbox
              checked={task.completed}
              onCheckedChange={(checked: boolean | 'indeterminate') =>
                setTasks((prev) =>
                  prev.map((t) => (t.id === task.id ? { ...t, completed: Boolean(checked) } : t))
                )
              }
              className="w-4 h-4 rounded border-muted-foreground/40 data-[state=checked]:bg-muted data-[state=checked]:border-muted"
            />
            <span
              className={cn(
                'flex-1 text-sm',
                task.completed ? 'text-muted-foreground line-through' : 'text-foreground'
              )}
            >
              {task.title}
            </span>
            <div className="w-5 h-5 rounded-full bg-secondary flex items-center justify-center shrink-0">
              <span className="text-[9px] text-muted-foreground">{task.assignee}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
