// Mirrors flowsint_core.himera.schemas.HimeraExtractionResult (JSON as stored by the API).

export interface HimeraFieldValue {
  name: string
  value: string
}

export interface HimeraRecord {
  record_id: string
  section: string
  source_name: string | null
  source_heading: string
  source_period: string | null
  page_start: number
  page_end: number
  fields: HimeraFieldValue[]
  name_match_only: boolean
  open_ended: boolean
}

export interface HimeraMention {
  record_id: string
  page: number
  raw_value: string
  evidence: string
  confidence: number
}

export interface HimeraEntity {
  entity_id: string
  type: string
  raw_value: string
  normalized_value: string
  confidence: number
  subject_link: string
  role: string | null
  attributes: Record<string, string>
  mentions: HimeraMention[]
  verification: 'validated' | 'unverified'
  issues: string[]
}

export interface HimeraRelationship {
  rel_id: string
  type: string
  source_entity_id: string
  target_entity_id: string
  record_id: string
  page_start: number
  page_end: number
  evidence: string
  confidence: number
  status: 'confirmed' | 'candidate' | 'source_claim'
  issues: string[]
}

export interface HimeraResult {
  report_id: string | null
  page_count: number
  records: HimeraRecord[]
  entities: HimeraEntity[]
  unverified_entities: HimeraEntity[]
  relationships: HimeraRelationship[]
  ambiguous_relationships: HimeraRelationship[]
  rejected: { kind: string; reason: string; data: Record<string, unknown> }[]
  warnings: string[]
  truncation_notice: boolean
  complete: boolean
  usage: {
    model: string
    pdf_engine: string
    calls: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd: number
  }
}

export interface HimeraImport {
  id: string
  sketch_id: string
  filename: string | null
  status: 'processing' | 'completed' | 'failed'
  error: string | null
  model: string | null
  cost_usd: number | null
  result: HimeraResult | null
  import_summary: Record<string, unknown> | null
  reused: boolean
}

export interface HimeraExecuteResult {
  status: 'completed' | 'completed_with_errors'
  nodes_written: number
  edges_written: number
  skipped_relationships: number
  errors: string[]
}
