import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({ fetchWithAuth: vi.fn() }))

import { fetchWithAuth } from './api'
import { flowService } from './flow-service'

const resolveEnricher = {
  name: 'domain_to_ip',
  class_name: 'ResolveEnricher',
  description: 'Resolve domain names to IP addresses.'
}

describe('flowService raw materials localization', () => {
  beforeEach(() => {
    vi.mocked(fetchWithAuth).mockReset()
  })

  it('translates enricher descriptions grouped by category', async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ items: { Domain: [resolveEnricher] } })

    const result = await flowService.getRawMaterial()

    const [enricher] = result.items.Domain
    expect(enricher.description).toBe('Определение IP-адресов по доменным именам.')
    // Identifiers stay untouched: the orchestrator resolves enrichers by name.
    expect(enricher.name).toBe('domain_to_ip')
  })

  it('translates enricher descriptions for one input type', async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue({ items: [resolveEnricher] })

    const result = await flowService.getRawMaterialForType('Domain')

    expect(result.items[0].description).toBe('Определение IP-адресов по доменным именам.')
  })
})
