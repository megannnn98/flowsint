import { memo } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../ui/dialog'
import { DynamicForm } from '../sketches/dynamic-form'

import { t } from '@/i18n'
interface TestFlowProps {
  open: boolean
  setOpen: (open: boolean) => void // This should accept a boolean parameter
  loading: boolean
  type: string
  onSubmit: (data: any) => Promise<void>
}

const TestFlow = memo(({ open, setOpen, type, loading, onSubmit }: TestFlowProps) => {
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogTitle>{t('Test flow')}</DialogTitle>
        <DialogDescription>{t('Fill the required data')}</DialogDescription>
        <DynamicForm currentNodeType={type} isForm={true} loading={loading} onSubmit={onSubmit} />
      </DialogContent>
    </Dialog>
  )
})

export default TestFlow
