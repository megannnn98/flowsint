import { beforeEach, describe, expect, it, vi } from 'vitest'

const confirm = vi.fn()
const mutateAsync = vi.fn()

vi.mock('sonner', () => ({ toast: { promise: vi.fn(), error: vi.fn() } }))
vi.mock('@/components/use-confirm-dialog', () => ({ useConfirm: () => ({ confirm }) }))
vi.mock('@tanstack/react-query', () => ({
  useMutation: () => ({ mutateAsync }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() })
}))
vi.mock('@/stores/layout-store', () => ({
  useLayoutStore: (select: (s: { openConsole: () => void }) => unknown) =>
    select({ openConsole: vi.fn() })
}))
vi.mock('@/api/flow-service', () => ({ flowService: { launch: vi.fn() } }))

import { toast } from 'sonner'
import { useLaunchFlow } from './use-launch-flow'

const FLOW_ID = '3f2b9c1e-0000-4000-8000-000000000000'

describe('useLaunchFlow', () => {
  beforeEach(() => {
    vi.mocked(toast.promise).mockClear()
    confirm.mockReset().mockResolvedValue(true)
    mutateAsync.mockReset().mockResolvedValue({})
  })

  it('shows the flow name, not its id, in the confirmation', async () => {
    await useLaunchFlow(true).launchFlow(['n1', 'n2'], FLOW_ID, 'sketch', 'Домен: IP и whois')

    const { title, message } = confirm.mock.calls[0][0]
    expect(title).toBe('Запуск: Домен: IP и whois')
    expect(message).toBe('Вы собираетесь запустить поток Домен: IP и whois для элементов: 2.')
  })

  it('reports the launch in Russian', async () => {
    await useLaunchFlow().launchFlow(['n1'], FLOW_ID, 'sketch', 'Домен: IP и whois')

    const options = vi.mocked(toast.promise).mock.calls[0][1] as Record<string, () => string>
    expect(options.loading).toBe('Загрузка...')
    expect(options.success()).toBe('Поток Домен: IP и whois запущен для элементов: 1.')
    expect(options.error()).toBe('Ошибка при запуске потока.')
  })

  it('falls back to the flow id when the name is unknown', async () => {
    await useLaunchFlow().launchFlow(['n1'], FLOW_ID, 'sketch')

    const options = vi.mocked(toast.promise).mock.calls[0][1] as Record<string, () => string>
    expect(options.success()).toBe(`Поток ${FLOW_ID} запущен для элементов: 1.`)
  })
})
