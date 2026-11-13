import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

import { toast } from 'sonner'
import { useFlowStore } from './flow-store'

const connect = (sourceHandle: string, targetHandle: string) =>
  useFlowStore.getState().onConnect({
    source: 'a',
    target: 'b',
    sourceHandle,
    targetHandle
  })

describe('flow store onConnect', () => {
  beforeEach(() => {
    useFlowStore.setState({ edges: [] })
    vi.mocked(toast.error).mockClear()
  })

  it('connects matching types', () => {
    connect('Domain', 'Domain')
    expect(useFlowStore.getState().edges).toHaveLength(1)
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('connects any type to an Any input', () => {
    connect('Domain', 'Any')
    expect(useFlowStore.getState().edges).toHaveLength(1)
  })

  it('rejects mismatched types', () => {
    connect('Domain', 'Ip')
    expect(useFlowStore.getState().edges).toHaveLength(0)
    expect(toast.error).toHaveBeenCalledOnce()
  })

  it('rejects an Any output into a typed input', () => {
    connect('Any', 'Domain')
    expect(useFlowStore.getState().edges).toHaveLength(0)
  })
})
