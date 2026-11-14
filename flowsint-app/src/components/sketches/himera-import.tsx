import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FileText, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/table'
import { sketchService } from '@/api/sketch-service'
import { useGraphControls } from '@/stores/graph-controls-store'
import { cn } from '@/lib/utils'
import Loader from '../loader'
import type { HimeraEntity, HimeraImport, HimeraRelationship, HimeraResult } from '@/types/himera'
import { t } from '@/i18n'

interface HimeraImportPanelProps {
  sketchId: string
  onSuccess: () => void
  onCancel: () => void
  onResultChange?: (hasResult: boolean) => void
}

const POLL_MS = 2000

function BalanceLine({ sketchId }: { sketchId: string }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ['openrouter-balance', sketchId],
    queryFn: () => sketchService.getOpenRouterBalance(sketchId),
    staleTime: 30_000,
    retry: false
  })
  if (isLoading) {
    return <span className="text-xs text-muted-foreground">{t('OpenRouter balance')}: …</span>
  }
  if (error || !data) {
    return (
      <span className="text-xs text-amber-600">
        {t('OpenRouter balance')}: {(error as Error)?.message ?? t('unavailable')}
      </span>
    )
  }
  const value = data.remaining_usd === null ? t('no limit') : `$${data.remaining_usd.toFixed(2)}`
  return (
    <span
      className={cn(
        'text-xs',
        data.remaining_usd !== null && data.remaining_usd < 1
          ? 'text-red-600 font-semibold'
          : 'text-muted-foreground'
      )}
      title={
        data.source === 'key'
          ? t('Remaining limit of this API key')
          : t('Account credits minus usage')
      }
    >
      {t('OpenRouter balance')}: {value}
    </span>
  )
}

// Share of the bar per stage: records pass, entities pass, validation.
const STAGES: Record<string, { label: string; from: number; to: number }> = {
  preparing: { label: 'Preparing the PDF', from: 0, to: 2 },
  records: { label: 'Step 1 of 2: reading records', from: 2, to: 50 },
  entities: { label: 'Step 2 of 2: extracting entities and links', from: 50, to: 95 },
  validating: { label: 'Checking and saving', from: 95, to: 100 }
}

function AnalysisProgress({
  progress,
  startedAt
}: {
  progress: HimeraImport['progress']
  startedAt: number
}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  const stage = STAGES[progress?.stage ?? 'preparing'] ?? STAGES.preparing
  const done = progress?.done ?? 0
  const total = progress?.total ?? 0
  const share = total > 0 ? Math.min(done / total, 1) : 0
  const percent = Math.round(stage.from + (stage.to - stage.from) * share)
  const seconds = Math.max(0, Math.round((now - startedAt) / 1000))
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
  return (
    <div className="flex flex-col gap-3 py-12 px-4 w-full max-w-xl mx-auto">
      <div className="flex justify-between text-sm">
        <span className="font-medium">{t(stage.label)}</span>
        <span className="text-muted-foreground">{percent}%</span>
      </div>
      <Progress value={percent} />
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>
          {total > 0 && progress?.stage !== 'validating'
            ? t('{{done}} of {{total}} parts done', { done, total })
            : ' '}
        </span>
        <span>
          {t('Spent')}: ${(progress?.cost_usd ?? 0).toFixed(3)} · {elapsed}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        {t('The PDF is being analysed by the language model. This can take a few minutes.')}
      </p>
    </div>
  )
}

function pagesOf(entity: HimeraEntity): string {
  return [...new Set(entity.mentions.map((m) => m.page))].sort((a, b) => a - b).join(', ')
}

function recordsOf(entity: HimeraEntity): string {
  return [...new Set(entity.mentions.map((m) => m.record_id))].join(', ')
}

function toggle(set: Set<string>, id: string): Set<string> {
  const next = new Set(set)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  return next
}

function EntityTable({
  entities,
  selected,
  onToggle
}: {
  entities: HimeraEntity[]
  selected: Set<string>
  onToggle: (id: string) => void
}) {
  if (!entities.length) {
    return <p className="text-sm text-muted-foreground p-4">{t('Nothing found')}</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>{t('Type')}</TableHead>
          <TableHead>{t('Value')}</TableHead>
          <TableHead>{t('Original value')}</TableHead>
          <TableHead>{t('Records')}</TableHead>
          <TableHead>{t('Pages')}</TableHead>
          <TableHead>{t('Confidence')}</TableHead>
          <TableHead>{t('Remarks')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entities.map((e) => (
          <TableRow key={e.entity_id}>
            <TableCell>
              <Checkbox
                checked={selected.has(e.entity_id)}
                onCheckedChange={() => onToggle(e.entity_id)}
              />
            </TableCell>
            <TableCell>
              <Badge variant="outline">{t(e.type.toLowerCase())}</Badge>
              {e.subject_link === 'subject' && (
                <Badge className="ml-1">{t('report subject')}</Badge>
              )}
              {e.type === 'Individual' && e.subject_link !== 'subject' && (
                <Badge variant="secondary" className="ml-1">
                  {t('candidate')}
                </Badge>
              )}
            </TableCell>
            <TableCell className="font-medium break-all">{e.normalized_value}</TableCell>
            <TableCell className="text-muted-foreground break-all">{e.raw_value}</TableCell>
            <TableCell className="text-xs">{recordsOf(e)}</TableCell>
            <TableCell className="text-xs">{pagesOf(e)}</TableCell>
            <TableCell className="text-xs">{e.confidence.toFixed(2)}</TableCell>
            <TableCell className="text-xs text-amber-600">{e.issues.join('; ')}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function RelationshipTable({
  relationships,
  labels,
  selected,
  onToggle
}: {
  relationships: HimeraRelationship[]
  labels: Map<string, string>
  selected: Set<string>
  onToggle: (id: string) => void
}) {
  if (!relationships.length) {
    return <p className="text-sm text-muted-foreground p-4">{t('Nothing found')}</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>{t('Relationship')}</TableHead>
          <TableHead>{t('From')}</TableHead>
          <TableHead>{t('To')}</TableHead>
          <TableHead>{t('Status')}</TableHead>
          <TableHead>{t('Record')}</TableHead>
          <TableHead>{t('Pages')}</TableHead>
          <TableHead>{t('Evidence')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {relationships.map((r) => (
          <TableRow key={r.rel_id}>
            <TableCell>
              <Checkbox
                checked={selected.has(r.rel_id)}
                onCheckedChange={() => onToggle(r.rel_id)}
              />
            </TableCell>
            <TableCell className="font-mono text-xs">{r.type}</TableCell>
            <TableCell className="break-all">{labels.get(r.source_entity_id)}</TableCell>
            <TableCell className="break-all">{labels.get(r.target_entity_id)}</TableCell>
            <TableCell>
              <Badge variant={r.status === 'confirmed' ? 'default' : 'secondary'}>
                {t(r.status)}
              </Badge>
            </TableCell>
            <TableCell className="text-xs">{r.record_id}</TableCell>
            <TableCell className="text-xs">
              {r.page_start === r.page_end ? r.page_start : `${r.page_start}-${r.page_end}`}
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {r.evidence}
              {r.issues.length > 0 && (
                <span className="block text-amber-600">{r.issues.join('; ')}</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Preview({
  sketchId,
  data,
  result,
  onSuccess,
  onCancel
}: {
  sketchId: string
  data: HimeraImport
  result: HimeraResult
  onSuccess: () => void
  onCancel: () => void
}) {
  const refetchGraph = useGraphControls((s) => s.refetchGraph)
  const queryClient = useQueryClient()
  const [entities, setEntities] = useState(() => new Set(result.entities.map((e) => e.entity_id)))
  const [relationships, setRelationships] = useState(
    () => new Set(result.relationships.map((r) => r.rel_id))
  )
  const [includeRecords, setIncludeRecords] = useState(true)
  const [importing, setImporting] = useState(false)

  const labels = useMemo(() => {
    const map = new Map<string, string>()
    for (const e of [...result.entities, ...result.unverified_entities]) {
      map.set(e.entity_id, e.normalized_value)
    }
    return map
  }, [result])

  const runImport = useCallback(async () => {
    setImporting(true)
    try {
      const res = await sketchService.executeHimeraImport(sketchId, data.id, {
        entity_ids: [...entities],
        relationship_ids: [...relationships],
        include_source_records: includeRecords
      })
      if (res.status === 'completed') {
        toast.success(
          t('Imported: {{nodes}} nodes, {{edges}} relationships', {
            nodes: res.nodes_written,
            edges: res.edges_written
          })
        )
      } else {
        toast.warning(t('Import Completed with Errors'), {
          description: res.errors.slice(0, 3).join('\n')
        })
      }
      refetchGraph()
      void queryClient.invalidateQueries({ queryKey: ['openrouter-balance', sketchId] })
      onSuccess()
    } catch (error) {
      toast.error((error as Error)?.message)
    } finally {
      setImporting(false)
    }
  }, [
    sketchId,
    data.id,
    entities,
    relationships,
    includeRecords,
    refetchGraph,
    onSuccess,
    queryClient
  ])

  const u = result.usage
  return (
    <div className="flex flex-col grow overflow-hidden gap-3">
      <div className="text-sm flex flex-wrap gap-x-4 gap-y-1">
        <span>
          {t('Records')}: <b>{result.records.length}</b>
        </span>
        <span>
          {t('Entities')}: <b>{result.entities.length}</b>
        </span>
        <span>
          {t('Unverified')}: <b>{result.unverified_entities.length}</b>
        </span>
        <span>
          {t('Model')}: <b>{u.model}</b> ({u.pdf_engine})
        </span>
        <span>
          {t('Tokens')}: {u.prompt_tokens} / {u.completion_tokens}
        </span>
        <span>
          {t('Cost')}: ${u.cost_usd.toFixed(4)}
        </span>
        {data.reused && <Badge variant="secondary">{t('cached analysis')}</Badge>}
        <BalanceLine sketchId={sketchId} />
      </div>
      {(!result.complete || result.warnings.length > 0) && (
        <div
          className={cn(
            'text-xs rounded border p-2 max-h-24 overflow-auto',
            result.complete ? 'border-amber-300' : 'border-red-400'
          )}
        >
          {!result.complete && (
            <p className="font-semibold text-red-600">
              {t('The analysis is incomplete: some records may be missing.')}
            </p>
          )}
          {result.warnings.map((w, i) => (
            <p key={i}>{w}</p>
          ))}
        </div>
      )}
      <Tabs defaultValue="entities" className="flex flex-col grow overflow-hidden">
        <TabsList>
          <TabsTrigger value="entities">
            {t('Entities')} ({result.entities.length})
          </TabsTrigger>
          <TabsTrigger value="unverified">
            {t('Unverified')} ({result.unverified_entities.length})
          </TabsTrigger>
          <TabsTrigger value="relationships">
            {t('Relationships')} ({result.relationships.length})
          </TabsTrigger>
          <TabsTrigger value="ambiguous">
            {t('Ambiguous relationships')} ({result.ambiguous_relationships.length})
          </TabsTrigger>
          <TabsTrigger value="records">
            {t('Records')} ({result.records.length})
          </TabsTrigger>
        </TabsList>
        <div className="grow overflow-auto">
          <TabsContent value="entities">
            <EntityTable
              entities={result.entities}
              selected={entities}
              onToggle={(id) => setEntities((s) => toggle(s, id))}
            />
          </TabsContent>
          <TabsContent value="unverified">
            <p className="text-xs text-muted-foreground p-2">
              {t(
                'Values that failed format checks or were not found in the cited record. They are not merged automatically; import only after manual review.'
              )}
            </p>
            <EntityTable
              entities={result.unverified_entities}
              selected={entities}
              onToggle={(id) => setEntities((s) => toggle(s, id))}
            />
          </TabsContent>
          <TabsContent value="relationships">
            <RelationshipTable
              relationships={result.relationships}
              labels={labels}
              selected={relationships}
              onToggle={(id) => setRelationships((s) => toggle(s, id))}
            />
          </TabsContent>
          <TabsContent value="ambiguous">
            <p className="text-xs text-muted-foreground p-2">
              {t(
                'Candidates (name-only matches, unknown identity) and source claims (labels, scores, registries). They are statements of the source, not verified facts.'
              )}
            </p>
            <RelationshipTable
              relationships={result.ambiguous_relationships}
              labels={labels}
              selected={relationships}
              onToggle={(id) => setRelationships((s) => toggle(s, id))}
            />
          </TabsContent>
          <TabsContent value="records">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>{t('Source')}</TableHead>
                  <TableHead>{t('Pages')}</TableHead>
                  <TableHead>{t('Fields')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.records.map((r) => (
                  <TableRow key={r.record_id}>
                    <TableCell className="text-xs font-mono">{r.record_id}</TableCell>
                    <TableCell>
                      {r.source_heading}
                      {r.name_match_only && (
                        <Badge variant="secondary" className="ml-1">
                          {t('name match only')}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.page_start === r.page_end ? r.page_start : `${r.page_start}-${r.page_end}`}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {r.fields.map((f) => `${f.name}: ${f.value}`).join('; ')}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TabsContent>
        </div>
      </Tabs>
      <div className="flex items-center justify-between border-t pt-3 shrink-0">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={includeRecords}
            onCheckedChange={(v) => setIncludeRecords(Boolean(v))}
          />
          {t('Add source record nodes (provenance)')}
        </label>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onCancel} disabled={importing}>
            {t('Cancel')}
          </Button>
          <Button onClick={runImport} disabled={importing || entities.size === 0}>
            {importing
              ? t('Importing...')
              : t('Import {{count}} entities', { count: entities.size })}
          </Button>
        </div>
      </div>
    </div>
  )
}

export function HimeraImportPanel({
  sketchId,
  onSuccess,
  onCancel,
  onResultChange
}: HimeraImportPanelProps) {
  const [importId, setImportId] = useState<string | null>(null)
  const [startedAt, setStartedAt] = useState(() => Date.now())
  const [uploading, setUploading] = useState(false)

  const { data } = useQuery({
    queryKey: ['himera-import', sketchId, importId],
    queryFn: () => sketchService.getHimeraImport(sketchId, importId as string),
    enabled: Boolean(importId),
    refetchInterval: (query) =>
      query.state.data?.status === 'processing' || !query.state.data ? POLL_MS : false
  })

  const hasResult = data?.status === 'completed' && Boolean(data.result)
  useEffect(() => {
    onResultChange?.(hasResult)
  }, [hasResult, onResultChange])

  const upload = useCallback(
    async (file: File, force = false) => {
      if (!file.name.toLowerCase().endsWith('.pdf')) {
        toast.error(t('Only .pdf files are supported'))
        return
      }
      setUploading(true)
      try {
        setStartedAt(Date.now())
        const started = await sketchService.startHimeraImport(sketchId, file, force)
        setImportId(started.id)
      } catch (error) {
        toast.error((error as Error)?.message)
      } finally {
        setUploading(false)
      }
    },
    [sketchId]
  )

  const reset = useCallback(() => {
    setImportId(null)
    onCancel()
  }, [onCancel])

  if (data?.status === 'completed' && data.result) {
    return (
      <Preview
        sketchId={sketchId}
        data={data}
        result={data.result}
        onSuccess={onSuccess}
        onCancel={reset}
      />
    )
  }

  if (data?.status === 'failed') {
    return (
      <div className="flex flex-col gap-4 p-4 border rounded-lg border-red-400">
        <p className="font-medium">{t('Analysis failed')}</p>
        <p className="text-sm text-muted-foreground break-all">{data.error}</p>
        <Button variant="outline" onClick={reset}>
          {t('Try again')}
        </Button>
      </div>
    )
  }

  if (importId || uploading) {
    if (uploading || !data) {
      return (
        <div className="flex flex-col items-center justify-center gap-4 py-12">
          <Loader />
          <p className="text-sm text-muted-foreground">{t('Uploading...')}</p>
        </div>
      )
    }
    return <AnalysisProgress progress={data.progress} startedAt={startedAt} />
  }

  return (
    <div className="border-2 border-dashed rounded-lg p-12 text-center flex items-center justify-center grow border-muted-foreground/25">
      <div className="flex flex-col items-center gap-4">
        <div className="flex gap-2 text-muted-foreground">
          <Upload className="h-12 w-12" />
          <FileText className="h-12 w-12" />
        </div>
        <p className="text-lg font-medium">{t('Himera Search PDF report')}</p>
        <BalanceLine sketchId={sketchId} />
        <p className="text-sm text-muted-foreground max-w-md">
          {t(
            'The report is sent to OpenRouter for analysis. It contains personal data: make sure this is allowed for your investigation and provider settings.'
          )}
        </p>
        <input
          type="file"
          id="himera-upload"
          className="hidden"
          accept=".pdf"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) {
              void upload(file)
            }
          }}
        />
        <Button asChild variant="outline">
          <label htmlFor="himera-upload" className="cursor-pointer">
            {t('Choose PDF')}
          </label>
        </Button>
      </div>
    </div>
  )
}
