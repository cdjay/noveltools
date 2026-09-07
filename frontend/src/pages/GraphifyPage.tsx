import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from 'react'
import { Network, RefreshCw, Route, SearchCheck } from 'lucide-react'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Analysis, ChangeLog, GraphNode, GraphResult, GraphifyProposal, GraphifyRun, PlotThread } from '../types'
import { AnalysisResultDetails } from '../components/AnalysisResultDetails'
import { Badge, Button, Empty, Field, InlineError, InlineNotice, Input, Loading, MetaText, PageHeader, Panel, Select, Stat, StatusBadge, Tabs, Textarea } from '../lib/ui'

type Tab = 'review' | 'init' | 'status' | 'threads' | 'graph' | 'logs'

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '未提供'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function ProposalCards({ run, onAction }: { run: GraphifyRun; onAction: (proposal: GraphifyProposal, type: 'accept' | 'reject') => void }) {
  if (!run.proposals.length) return <Empty compact title="没有待确认提案" text="模型没有返回带证据的可写入内容，或者本次结果只包含审阅建议。" />
  return <div className="space-y-3">{run.proposals.map(proposal => <div key={proposal.id} className="rounded-md border border-warn/40 bg-warn-soft/60 p-4 text-body">
    <div className="flex items-center justify-between gap-3">
      <div className="font-semibold text-ink">{proposal.target_name || proposal.change_type}</div>
      <StatusBadge status={proposal.status} />
    </div>
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      <div><MetaText>原值</MetaText><pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-paper-raised/70 p-2 text-meta text-ink-soft">{valueText(proposal.old_value)}</pre></div>
      <div><MetaText>建议值</MetaText><pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-paper-raised/70 p-2 text-meta text-ink-soft">{valueText(proposal.proposed_value)}</pre></div>
    </div>
    <p className="mt-3 text-ink-soft">修改原因：{proposal.reason || '未提供'}</p>
    <MetaText className="mt-1">证据：{proposal.evidence || '未提供'} · 置信度：{Math.round((proposal.confidence || 0) * 100)}%</MetaText>
    {proposal.status === 'pending' && <div className="mt-3 flex gap-2">
      <Button size="sm" variant="primary" onClick={() => onAction(proposal, 'accept')}>接受写入</Button>
      <Button size="sm" variant="danger" onClick={() => onAction(proposal, 'reject')}>拒绝</Button>
    </div>}
  </div>)}</div>
}

function runAsAnalysis(run: GraphifyRun): Analysis {
  return { id: run.id, book_id: run.book_id, chapter_id: 0, status: run.status, result_json: run.result_json as Analysis['result_json'], raw_response: run.raw_response, model: run.model, elapsed_ms: run.elapsed_ms, error_message: run.error_message, created_at: run.created_at, changes: run.proposals.map(item => ({ ...item, analysis_id: run.id })) } as Analysis
}

/* ---------- 关系图画布（浅色主题） ---------- */

type GraphPoint = { x: number; y: number }
type GraphInteraction = { type: 'pan' | 'node'; id?: string; clientX: number; clientY: number; startPan: GraphPoint; startNode?: GraphPoint }

const GRAPH_WIDTH = 1200
const GRAPH_HEIGHT = 720
const GRAPH_COLORS: Record<string, string> = { character: '#476e71', knowledge: '#5f7595', event: '#a97c2f', thread: '#8a7fae', chapter: '#938d7e' }
const GRAPH_LABELS: Record<string, string> = { character: '角色', knowledge: '知识', event: '事件', thread: '情节线', chapter: '章节' }

function GraphCanvas({ graph }: { graph: GraphResult | null }) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [selectedKinds, setSelectedKinds] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState<GraphPoint>({ x: 0, y: 0 })
  const [interaction, setInteraction] = useState<GraphInteraction | null>(null)
  const [nodePositions, setNodePositions] = useState<Record<string, GraphPoint>>({})

  const allKinds = useMemo(() => Array.from(new Set((graph?.nodes || []).map(node => node.kind))), [graph])
  const basePositions = useMemo(() => {
    const positions: Record<string, GraphPoint> = {}
    const groups = new Map<string, GraphNode[]>()
    for (const node of graph?.nodes || []) groups.set(node.kind, [...(groups.get(node.kind) || []), node])
    const groupEntries = Array.from(groups.entries())
    groupEntries.forEach(([kind, items], groupIndex) => {
      const centerAngle = (groupIndex / Math.max(1, groupEntries.length)) * Math.PI * 2 - Math.PI / 2
      const centerRadius = Math.min(250, 150 + groupEntries.length * 15)
      const center = { x: GRAPH_WIDTH / 2 + Math.cos(centerAngle) * centerRadius, y: GRAPH_HEIGHT / 2 + Math.sin(centerAngle) * centerRadius }
      const radius = Math.max(75, Math.min(260, 58 + items.length * 4))
      items.forEach((node, index) => {
        const angle = (index / Math.max(1, items.length)) * Math.PI * 2
        positions[node.id] = { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius }
      })
    })
    return positions
  }, [graph])

  useEffect(() => { setSelectedKinds(allKinds); setNodePositions(basePositions); setSelectedNodeId(null); setPan({ x: 0, y: 0 }); setZoom(1) }, [allKinds, basePositions])

  const visibleNodes = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase()
    return (graph?.nodes || []).filter(node => selectedKinds.includes(node.kind) && (!keyword || `${node.label} ${node.description}`.toLocaleLowerCase().includes(keyword)))
  }, [graph, search, selectedKinds])
  const visibleIds = useMemo(() => new Set(visibleNodes.map(node => node.id)), [visibleNodes])
  const visibleEdges = useMemo(() => (graph?.edges || []).filter(edge => visibleIds.has(edge.from) && visibleIds.has(edge.to)), [graph, visibleIds])
  const selectedNode = graph?.nodes.find(node => node.id === selectedNodeId) || null
  const connectedIds = useMemo(() => {
    if (!selectedNodeId) return new Set<string>()
    const ids = new Set([selectedNodeId])
    for (const edge of graph?.edges || []) if (edge.from === selectedNodeId) ids.add(edge.to); else if (edge.to === selectedNodeId) ids.add(edge.from)
    return ids
  }, [graph, selectedNodeId])

  if (!graph || !graph.nodes.length) return <Empty icon={<Network size={20} />} title="还没有图谱节点" text="先建立角色、知识项、事件或情节线，图谱会从这些资料自动生成。" />

  const pointFor = (id: string) => nodePositions[id] || basePositions[id] || { x: GRAPH_WIDTH / 2, y: GRAPH_HEIGHT / 2 }
  const toWorld = (event: ReactPointerEvent<SVGElement> | ReactWheelEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return { x: GRAPH_WIDTH / 2, y: GRAPH_HEIGHT / 2 }
    const screenX = ((event.clientX - rect.left) / rect.width) * GRAPH_WIDTH
    const screenY = ((event.clientY - rect.top) / rect.height) * GRAPH_HEIGHT
    return { x: (screenX - pan.x) / zoom, y: (screenY - pan.y) / zoom }
  }
  const startInteraction = (event: ReactPointerEvent<SVGElement>, type: GraphInteraction['type'], id?: string) => {
    event.preventDefault()
    svgRef.current?.setPointerCapture(event.pointerId)
    setInteraction({ type, id, clientX: event.clientX, clientY: event.clientY, startPan: pan, startNode: id ? pointFor(id) : undefined })
    if (id) setSelectedNodeId(id)
  }
  const moveInteraction = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!interaction) return
    if (interaction.type === 'node' && interaction.id) { setNodePositions(previous => ({ ...previous, [interaction.id!]: toWorld(event) })); return }
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    setPan({ x: interaction.startPan.x + ((event.clientX - interaction.clientX) / rect.width) * GRAPH_WIDTH, y: interaction.startPan.y + ((event.clientY - interaction.clientY) / rect.height) * GRAPH_HEIGHT })
  }
  const finishInteraction = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId)
    setInteraction(null)
  }
  const changeZoom = (nextZoom: number, event?: ReactWheelEvent<SVGSVGElement>) => {
    const clamped = Math.max(0.35, Math.min(2.5, nextZoom))
    if (!event) { setZoom(clamped); return }
    const cursor = toWorld(event)
    setPan(previous => ({ x: previous.x + (zoom - clamped) * cursor.x, y: previous.y + (zoom - clamped) * cursor.y }))
    setZoom(clamped)
  }
  const handleWheel = (event: ReactWheelEvent<SVGSVGElement>) => { event.preventDefault(); changeZoom(zoom * (event.deltaY < 0 ? 1.12 : 0.89), event) }
  const toggleKind = (kind: string) => setSelectedKinds(previous => previous.includes(kind) ? previous.filter(item => item !== kind) : [...previous, kind])
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); setNodePositions(basePositions) }
  const relatedEdges = selectedNodeId ? visibleEdges.filter(edge => edge.from === selectedNodeId || edge.to === selectedNodeId) : []

  return <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
    <div className="overflow-hidden rounded-lg border border-line bg-paper-raised">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft px-3 py-2 text-meta text-ink-faint">
        <span>节点 {visibleNodes.length} / {graph.nodes.length} · 关系 {visibleEdges.length} · 拖动平移，拖动节点调整位置，滚轮缩放</span>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" aria-label="放大" onClick={() => changeZoom(zoom * 1.15)}>＋</Button>
          <Button size="sm" variant="ghost" aria-label="缩小" onClick={() => changeZoom(zoom * 0.87)}>−</Button>
          <Button size="sm" variant="ghost" onClick={resetView}>重置视图</Button>
        </div>
      </div>
      <div className="overflow-auto scrollbar-thin">
        <svg ref={svgRef} viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`} width="100%" height="700" role="img" aria-label="可交互故事关系图" style={{ minWidth: 720, touchAction: 'none', cursor: interaction?.type === 'pan' ? 'grabbing' : 'grab' }} onWheel={handleWheel} onPointerDown={event => { if (event.target === event.currentTarget) startInteraction(event, 'pan') }} onPointerMove={moveInteraction} onPointerUp={finishInteraction} onPointerCancel={finishInteraction}>
          <defs><marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#b8b09a" /></marker></defs>
          <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
            {Array.from(new Set(visibleNodes.map(node => node.kind))).map((kind, index) => {
              const angle = (index / Math.max(1, allKinds.length)) * Math.PI * 2 - Math.PI / 2
              const center = { x: GRAPH_WIDTH / 2 + Math.cos(angle) * Math.min(250, 150 + allKinds.length * 15), y: GRAPH_HEIGHT / 2 + Math.sin(angle) * Math.min(250, 150 + allKinds.length * 15) }
              return <g key={`community-${kind}`}><circle cx={center.x} cy={center.y} r="115" fill={GRAPH_COLORS[kind] || '#938d7e'} opacity="0.07" /><text x={center.x} y={center.y - 98} textAnchor="middle" fill={GRAPH_COLORS[kind] || '#938d7e'} fontSize="13" fontWeight="600">{GRAPH_LABELS[kind] || kind}</text></g>
            })}
            {visibleEdges.map((edge, index) => {
              const from = pointFor(edge.from); const to = pointFor(edge.to)
              const emphasized = !!selectedNodeId && (edge.from === selectedNodeId || edge.to === selectedNodeId)
              return <g key={`${edge.from}-${edge.to}-${index}`} opacity={selectedNodeId && !emphasized ? 0.15 : 0.8}>
                <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke={emphasized ? '#314a4d' : '#c9c2af'} strokeWidth={emphasized ? 2.4 : 1.2} markerEnd="url(#graph-arrow)" />
                {emphasized && <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 5} fill="#314a4d" fontSize="11" fontWeight="500" textAnchor="middle">{edge.relation}</text>}
              </g>
            })}
            {visibleNodes.map(node => {
              const point = pointFor(node.id)
              const active = !selectedNodeId || connectedIds.has(node.id)
              const selected = node.id === selectedNodeId
              const color = GRAPH_COLORS[node.kind] || '#938d7e'
              return <g key={node.id} opacity={active ? 1 : 0.18} onPointerDown={event => startInteraction(event, 'node', node.id)} onClick={() => setSelectedNodeId(node.id)}>
                <circle cx={point.x} cy={point.y} r={selected ? 29 : 22} fill={color} stroke={selected ? '#2b2822' : '#fffefb'} strokeWidth={selected ? 3 : 2} />
                <text x={point.x} y={point.y + 4} textAnchor="middle" fontSize="11" fill="white" pointerEvents="none">{node.label.length > 8 ? `${node.label.slice(0, 8)}…` : node.label}</text>
              </g>
            })}
          </g>
        </svg>
      </div>
    </div>
    <aside className="self-start rounded-lg border border-line bg-paper-raised p-4">
      <div className="text-[15px] font-semibold text-ink">图谱筛选</div>
      <MetaText className="mt-0.5">按节点类型查看社区</MetaText>
      <Input className="mt-3 py-1.5 text-meta" placeholder="搜索节点名称或描述" value={search} onChange={event => setSearch(event.target.value)} aria-label="搜索图谱节点" />
      <button className="mt-3 text-meta text-accent-700 underline underline-offset-2 hover:text-accent-600" onClick={() => setSelectedKinds(selectedKinds.length === allKinds.length ? [] : allKinds)}>{selectedKinds.length === allKinds.length ? '取消全选' : '选择全部'}</button>
      <div className="mt-2.5 space-y-2">{allKinds.map(kind => {
        const count = graph.nodes.filter(node => node.kind === kind).length
        const checked = selectedKinds.includes(kind)
        return <label key={kind} className="flex cursor-pointer items-center gap-2 text-body text-ink-soft">
          <input type="checkbox" className="accent-accent-600" checked={checked} onChange={() => toggleKind(kind)} />
          <i className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: GRAPH_COLORS[kind] || '#938d7e' }} />
          <span>{GRAPH_LABELS[kind] || kind}</span>
          <span className="ml-auto tabular-nums text-ink-faint">{count}</span>
        </label>
      })}</div>
      {selectedNode && <div className="mt-5 border-t border-line-soft pt-4">
        <MetaText>当前节点</MetaText>
        <div className="mt-1 text-[15px] font-semibold text-ink">{selectedNode.label}</div>
        <MetaText className="mt-0.5">{GRAPH_LABELS[selectedNode.kind] || selectedNode.kind}{selectedNode.status ? ` · ${selectedNode.status}` : ''}</MetaText>
        <p className="mt-2.5 max-h-36 overflow-auto whitespace-pre-wrap text-body leading-6 text-ink-soft scrollbar-thin">{selectedNode.description || '暂无描述'}</p>
        {relatedEdges.length > 0 && <div className="mt-3 space-y-1 text-body">
          <MetaText>关联关系</MetaText>
          {relatedEdges.slice(0, 12).map((edge, index) => {
            const otherId = edge.from === selectedNode.id ? edge.to : edge.from
            const other = graph.nodes.find(node => node.id === otherId)
            return <button className="block w-full truncate text-left text-meta text-ink-soft transition-colors hover:text-accent-700" key={`${otherId}-${index}`} onClick={() => setSelectedNodeId(otherId)}>{edge.relation} → {other?.label || otherId}</button>
          })}
        </div>}
      </div>}
    </aside>
  </div>
}

/* ---------- 页面主体 ---------- */

export function GraphifyPage() {
  const { currentBook, chapters } = useAppStore()
  const bookId = currentBook?.id
  const [tab, setTab] = useState<Tab>('review')
  const [selectedChapterId, setSelectedChapterId] = useState<number | undefined>(chapters[0]?.id)
  const [intent, setIntent] = useState('')
  const [premise, setPremise] = useState('')
  const [fromChapters, setFromChapters] = useState(true)
  const [batch, setBatch] = useState(5)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [run, setRun] = useState<GraphifyRun | null>(null)
  const [autoRuns, setAutoRuns] = useState<GraphifyRun[]>([])
  const [reviewRun, setReviewRun] = useState<GraphifyRun | null>(null)
  const [threads, setThreads] = useState<PlotThread[]>([])
  const [status, setStatus] = useState<any>(null)
  const [graph, setGraph] = useState<GraphResult | null>(null)
  const [fullGraph, setFullGraph] = useState<GraphResult | null>(null)
  const [logs, setLogs] = useState<ChangeLog[]>([])
  const [question, setQuestion] = useState('')
  const [dfs, setDfs] = useState(false)
  const [fromName, setFromName] = useState('')
  const [toName, setToName] = useState('')
  const [pathResult, setPathResult] = useState<GraphResult | null>(null)
  const [threadTitle, setThreadTitle] = useState('')
  const [threadDescription, setThreadDescription] = useState('')
  const [passage, setPassage] = useState('')
  const [loreName, setLoreName] = useState('')
  const [loreContent, setLoreContent] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const selectedChapter = useMemo(() => chapters.find(chapter => chapter.id === selectedChapterId), [chapters, selectedChapterId])

  const loadThreads = async () => { if (bookId) setThreads(await api<PlotThread[]>(`/books/${bookId}/threads`)) }
  const loadStatus = async () => { if (bookId) setStatus(await api(`/books/${bookId}/graphify/status`)) }
  const loadGraph = async () => { if (bookId) setFullGraph(await api<GraphResult>(`/books/${bookId}/graph`)) }
  const loadLogs = async () => { if (bookId) setLogs(await api<ChangeLog[]>(`/books/${bookId}/change-logs`)) }
  const loadAutoRuns = async () => { if (bookId) setAutoRuns((await api<GraphifyRun[]>(`/books/${bookId}/graphify/runs`)).filter(item => item.operation === 'auto_chapter')) }
  useEffect(() => { if (chapters.length && !selectedChapterId) setSelectedChapterId(chapters[0].id) }, [chapters, selectedChapterId])
  useEffect(() => { void loadThreads(); void loadStatus(); void loadLogs(); void loadAutoRuns() }, [bookId])
  useEffect(() => {
    const latest = autoRuns[0]
    if (!latest || !['queued', 'analyzing'].includes(latest.status)) return
    const timer = window.setInterval(() => { void loadAutoRuns() }, 3000)
    return () => window.clearInterval(timer)
  }, [autoRuns, bookId])
  useEffect(() => { if (tab === 'graph') void loadGraph(); if (tab === 'logs') void loadLogs() }, [tab, bookId])

  const runAnalysis = async (analysisType: 'review' | 'update') => {
    if (!selectedChapter) return
    setBusy(true); setError('')
    try { setAnalysis(await api<Analysis>(`/chapters/${selectedChapter.id}/analyze`, { method: 'POST', body: JSON.stringify({ analysis_type: analysisType, include_outline: true, include_previous_chapters: true, include_characters: true, include_knowledge: true, include_events: true, include_relations: true, include_threads: true, intent }) })) } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  const reviewPassage = async () => {
    if (!bookId || !passage.trim()) return
    setBusy(true); setError('')
    try { setReviewRun(await api<GraphifyRun>(`/books/${bookId}/graphify/review-passage`, { method: 'POST', body: JSON.stringify({ passage, intent }) })) } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  const init = async () => {
    if (!bookId || (!premise.trim() && !fromChapters)) return
    setBusy(true); setError('')
    try { setRun(await api<GraphifyRun>(`/books/${bookId}/graphify/init`, { method: 'POST', body: JSON.stringify({ premise, from_chapters: fromChapters, batch, intent }) })) } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  const proposeLore = async () => {
    if (!bookId || !loreName.trim() || !loreContent.trim()) return
    setBusy(true); setError('')
    try { setRun(await api<GraphifyRun>(`/books/${bookId}/graphify/lore`, { method: 'POST', body: JSON.stringify({ name: loreName, content: loreContent }) })); setLoreName(''); setLoreContent('') } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  const proposalAction = async (proposal: GraphifyProposal, type: 'accept' | 'reject') => {
    if (!run) return
    await api(`/graphify/runs/${run.id}/proposals/${proposal.id}/${type}`, { method: 'POST' })
    setRun(await api<GraphifyRun>(`/graphify/runs/${run.id}`)); void loadStatus(); void loadThreads(); void loadLogs(); void loadGraph()
  }
  const createThread = async () => {
    if (!bookId || !threadTitle.trim()) return
    await api(`/books/${bookId}/threads`, { method: 'POST', body: JSON.stringify({ title: threadTitle, description: threadDescription }) })
    setThreadTitle(''); setThreadDescription(''); void loadThreads(); void loadStatus()
  }
  const runQuery = async () => { if (bookId && question.trim()) setGraph(await api<GraphResult>(`/books/${bookId}/graphify/query`, { method: 'POST', body: JSON.stringify({ question, dfs }) })) }
  const runPath = async () => { if (bookId && fromName.trim() && toName.trim()) setPathResult(await api<GraphResult>(`/books/${bookId}/graphify/path`, { method: 'POST', body: JSON.stringify({ from_name: fromName, to_name: toName }) })) }

  const latestAuto = autoRuns[0]

  return <div className="p-6">
    <PageHeader title="故事图谱" description="把角色、世界、事件和情节线统一放进资料库与关系图。结构化且无冲突的分析结果会自动入库，原始记录与入库日志全部保留。" />
    <div className="mt-5 space-y-4">
      {error && <InlineError message={error} />}
      {/* 自动处理状态 */}
      {!latestAuto
        ? <InlineNotice tone="accent" message="自动分析：保存章节正文后会自动处理并入库；当前还没有自动运行记录。" />
        : <div className="flex flex-wrap items-center gap-2">
          <InlineNotice tone={latestAuto.status === 'failed' ? 'warn' : latestAuto.status === 'completed' ? 'ok' : 'accent'}
            message={`自动分析：${latestAuto.status === 'completed' ? '已完成' : latestAuto.status === 'failed' ? '失败' : latestAuto.status === 'analyzing' ? '分析中' : '排队中'}${latestAuto.request_context.chapter_number ? ` · 第 ${latestAuto.request_context.chapter_number} 章` : ''} · ${latestAuto.model}${latestAuto.error_message ? ` · ${latestAuto.error_message}` : ''}`} />
          <Button size="sm" variant="ghost" onClick={() => void loadAutoRuns()}><RefreshCw size={13} />刷新</Button>
        </div>}

      <Tabs value={tab} onChange={setTab} items={[['review', '审阅章节'], ['init', '初始化资料库'], ['status', '故事状态'], ['threads', '情节线'], ['graph', '关系图'], ['logs', '入库日志']]} />

      {tab === 'review' && <div className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
          <Panel title="审阅参数" className="self-start">
            <div className="space-y-4">
              <Field label="章节">
                <Select value={selectedChapterId || ''} onChange={e => setSelectedChapterId(Number(e.target.value))}>
                  {chapters.map(chapter => <option key={chapter.id} value={chapter.id}>第 {chapter.chapter_number} 章 {chapter.title}</option>)}
                </Select>
              </Field>
              <Field label="本章意图 / 重点" hint="告诉模型这一章想达成什么，审阅会更贴近你的目标。">
                <Textarea rows={4} value={intent} onChange={e => setIntent(e.target.value)} placeholder="例如：本章要让陈渡开始怀疑孟执事" />
              </Field>
              <div className="space-y-2">
                <Button variant="primary" className="w-full" disabled={!selectedChapter || busy} onClick={() => void runAnalysis('review')}>{busy ? '审阅中…' : '审阅本章'}</Button>
                <Button variant="secondary" className="w-full" disabled={!selectedChapter || busy} onClick={() => void runAnalysis('update')}>{busy ? '处理中…' : '从本章生成资料更新提案'}</Button>
              </div>
            </div>
          </Panel>
          <Panel title={selectedChapter ? `第 ${selectedChapter.chapter_number} 章审阅结果` : '审阅结果'}>
            {!analysis ? <Empty icon={<SearchCheck size={20} />} title="还没有审阅结果" text="选择章节后运行「审阅本章」查看连续性问题与开放线索；运行「更新提案」生成资料库变更建议。" /> : <div className="space-y-4">
              <MetaText>模型：{analysis.model} · 状态：{analysis.status} · 耗时 {analysis.elapsed_ms}ms</MetaText>
              <AnalysisResultDetails analysis={analysis} onChangeAction={async (change, type) => { await api(`/analysis/${analysis.id}/changes/${change.id}/${type}`, { method: 'POST' }); setAnalysis(await api<Analysis>(`/analysis/${analysis.id}`)) }} />
            </div>}
          </Panel>
        </div>
        <Panel title="审阅临时片段" description="还没有入库的段落也可以先审阅，不会影响任何已有资料。">
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
            <Textarea rows={4} value={passage} onChange={e => setPassage(e.target.value)} placeholder="粘贴一段正在写或准备写的文字…" />
            <Button variant="primary" className="self-start" disabled={busy || !passage.trim()} onClick={() => void reviewPassage()}>审阅这段文字</Button>
          </div>
          {reviewRun && <div className="mt-4 space-y-3">
            <MetaText>片段审阅 · {reviewRun.model} · {reviewRun.status}</MetaText>
            <AnalysisResultDetails analysis={runAsAnalysis(reviewRun)} onChangeAction={async (change, type) => { await api(`/graphify/runs/${reviewRun.id}/proposals/${change.id}/${type}`, { method: 'POST' }); setReviewRun(await api<GraphifyRun>(`/graphify/runs/${reviewRun.id}`)) }} />
          </div>}
        </Panel>
      </div>}

      {tab === 'init' && <div className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
          <Panel title="初始化资料库" className="self-start" description="从故事前提或已有章节提取角色、知识、事件和情节线草案。">
            <div className="space-y-4">
              <Field label="故事前提"><Textarea rows={6} value={premise} onChange={e => setPremise(e.target.value)} placeholder="输入一句或几段故事前提" /></Field>
              <label className="flex items-center gap-2 text-body text-ink-soft"><input type="checkbox" className="accent-accent-600" checked={fromChapters} onChange={e => setFromChapters(e.target.checked)} />同时扫描现有章节</label>
              <Field label="每批章节数"><Input type="number" min={1} max={20} value={batch} onChange={e => setBatch(Number(e.target.value))} /></Field>
              <Button variant="primary" className="w-full" disabled={busy || (!premise.trim() && !fromChapters)} onClick={() => void init()}>{busy ? '初始化分析中…' : '运行初始化'}</Button>
              <MetaText>结构化且无冲突的结果会自动写入；冲突或无法命名的内容只保留在运行记录中，不会强行入库。</MetaText>
            </div>
          </Panel>
          <Panel title="初始化记录">
            {!run ? <Empty title="暂无运行记录" text="运行初始化后，这里会显示结果、自动入库状态和原始记录。" /> : <div className="space-y-4">
              <MetaText>运行 #{run.id} · {run.model} · {run.status} · 结构化且无冲突的结果已自动处理，详情请查看入库日志。</MetaText>
              <ProposalCards run={run} onAction={proposalAction} />
            </div>}
          </Panel>
        </div>
        <Panel title="手动补充世界观 / 知识" description="直接把一条设定交给模型整理入库，会记录入库日志。">
          <div className="grid gap-3 md:grid-cols-[220px_minmax(0,1fr)_160px]">
            <Input placeholder="知识项名称" value={loreName} onChange={e => setLoreName(e.target.value)} />
            <Textarea rows={2} value={loreContent} onChange={e => setLoreContent(e.target.value)} placeholder="设定内容，例如：火窟位于北邙山腹，需令牌开启" />
            <Button variant="secondary" className="self-start" disabled={busy || !loreName.trim() || !loreContent.trim()} onClick={() => void proposeLore()}>提交并入库</Button>
          </div>
        </Panel>
      </div>}

      {tab === 'status' && <Panel title="故事状态" action={<Button size="sm" variant="ghost" onClick={() => { void loadStatus(); void loadThreads() }}><RefreshCw size={13} />刷新</Button>}>
        {!status ? <Loading text="正在读取故事状态…" /> : <div className="space-y-5">
          <div className="grid gap-3 md:grid-cols-4">
            <Stat label="开放情节线" value={status.open_threads?.length || 0} />
            <Stat label="未完成事件" value={status.unresolved_events?.length || 0} />
            <Stat label="未回收伏笔" value={status.unresolved_foreshadowing?.length || 0} />
            <Stat label="图谱节点 / 关系" value={`${status.node_count} / ${status.edge_count}`} />
          </div>
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <h3 className="text-[15px] font-semibold text-ink">当前开放情节线</h3>
              <div className="mt-3 space-y-2">
                {(status.open_threads || []).length === 0 && <MetaText>没有开放的情节线。</MetaText>}
                {(status.open_threads || []).map((item: PlotThread) => <div key={item.id} className="rounded-md bg-paper-sunken px-3.5 py-2.5 text-body">
                  <div className="font-medium text-ink">{item.title}</div>
                  <div className="mt-0.5 text-meta text-ink-soft">{item.unresolved_question || item.description || '暂无说明'}</div>
                </div>)}
              </div>
            </div>
            <div>
              <h3 className="text-[15px] font-semibold text-ink">结构枢纽</h3>
              <MetaText className="mt-0.5">连接最多的节点，通常是故事的关键人物或地点。</MetaText>
              <div className="mt-3 space-y-2">
                {(status.hubs || []).length === 0 && <MetaText>图谱中还没有足够的连接。</MetaText>}
                {(status.hubs || []).map((item: any) => <div key={item.id} className="flex items-center justify-between rounded-md bg-paper-sunken px-3.5 py-2.5 text-body">
                  <span className="text-ink">{item.label}</span>
                  <Badge tone="accent">{item.degree} 条连接</Badge>
                </div>)}
              </div>
            </div>
          </div>
        </div>}
      </Panel>}

      {tab === 'threads' && <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <Panel title="新增情节线" className="self-start">
          <div className="space-y-3">
            <Field label="名称"><Input placeholder="例如：陈渡的身世之谜" value={threadTitle} onChange={e => setThreadTitle(e.target.value)} /></Field>
            <Field label="描述"><Textarea rows={4} placeholder="这条线的未解问题或预期回收方式" value={threadDescription} onChange={e => setThreadDescription(e.target.value)} /></Field>
            <Button variant="primary" className="w-full" onClick={() => void createThread()}>创建情节线</Button>
          </div>
        </Panel>
        <Panel title={`情节线 · ${threads.length}`} action={<Button size="sm" variant="ghost" onClick={() => void loadThreads()}><RefreshCw size={13} />刷新</Button>}>
          {threads.length === 0 ? <Empty title="还没有情节线" text="可以手动创建，也可以从初始化或更新提案中确认加入。" /> : <div className="space-y-2.5">
            {threads.map(thread => <div key={thread.id} className="rounded-md border border-line-soft px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <div className="font-medium text-ink">{thread.title}</div>
                <StatusBadge status={thread.status} label={thread.status === 'resolved' ? '已解决' : '开放'} />
              </div>
              <p className="mt-1.5 text-body text-ink-soft">{thread.description || thread.unresolved_question || '暂无描述'}</p>
              {(thread.seed_chapter || thread.last_chapter) && <MetaText className="mt-1">起于第 {thread.seed_chapter || '?'} 章 · 最近出现于第 {thread.last_chapter || '?'} 章</MetaText>}
              {thread.status !== 'resolved' && <Button size="sm" variant="secondary" className="mt-2.5" onClick={async () => { await api(`/threads/${thread.id}/resolve`, { method: 'POST' }); void loadThreads(); void loadStatus() }}>标记已解决</Button>}
            </div>)}
          </div>}
        </Panel>
      </div>}

      {tab === 'graph' && <div className="space-y-4">
        <Panel title="关系图" description="图谱从已入库的角色、知识、事件、章节和情节线实时派生；它只展示关系，不直接修改资料库。" action={<Button size="sm" variant="ghost" onClick={() => void loadGraph()}><RefreshCw size={13} />刷新图谱</Button>}>
          <GraphCanvas graph={fullGraph} />
        </Panel>
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="关系查询" description="用自然语言问图谱，例如“陈渡和火窟有什么关系？”">
            <div className="flex flex-wrap gap-2">
              <Input className="min-w-56 flex-1" placeholder="输入你的问题" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => e.key === 'Enter' && void runQuery()} />
              <label className="flex items-center gap-2 px-1 text-body text-ink-soft"><input type="checkbox" className="accent-accent-600" checked={dfs} onChange={e => setDfs(e.target.checked)} />深度追踪</label>
              <Button variant="primary" onClick={() => void runQuery()}>查询</Button>
            </div>
            {graph && <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div>
                <MetaText className="mb-2">命中与关联节点</MetaText>
                <div className="space-y-2">{graph.nodes.map(node => <div key={node.id} className="rounded-md bg-paper-sunken px-3 py-2 text-body">
                  <div className="font-medium text-ink">{node.label}</div>
                  <MetaText>{GRAPH_LABELS[node.kind] || node.kind} · {node.description || '暂无描述'}</MetaText>
                </div>)}</div>
              </div>
              <div>
                <MetaText className="mb-2">关系</MetaText>
                <div className="space-y-2">{graph.edges.map((edge, index) => <div key={`${edge.from}-${edge.to}-${index}`} className="rounded-md bg-paper-sunken px-3 py-2 text-meta text-ink-soft">
                  {edge.from} — <span className="font-medium text-accent-700">{edge.relation}</span> → {edge.to}
                  {edge.note && <div className="mt-0.5 text-ink-faint">{edge.note}</div>}
                </div>)}</div>
              </div>
            </div>}
          </Panel>
          <Panel title="路径查询" description="查看两个节点之间如何通过事件与关系连接。">
            <div className="flex flex-wrap gap-2">
              <Input className="flex-1" placeholder="起点，例如：陈渡" value={fromName} onChange={e => setFromName(e.target.value)} />
              <Input className="flex-1" placeholder="终点，例如：火窟" value={toName} onChange={e => setToName(e.target.value)} />
              <Button variant="primary" onClick={() => void runPath()}><Route size={14} />查找路径</Button>
            </div>
            {pathResult && <div className="mt-4 rounded-md bg-paper-sunken px-3.5 py-3 text-body">
              <div className="text-ink-soft">{pathResult.message}</div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {(pathResult.path || []).map((node, index) => <span key={node.id} className="flex items-center gap-2">
                  <Badge tone="accent">{node.label}</Badge>
                  {index < (pathResult.path || []).length - 1 && <span className="text-ink-faint">→</span>}
                </span>)}
              </div>
            </div>}
          </Panel>
        </div>
      </div>}

      {tab === 'logs' && <Panel title="入库日志" description="每一次自动写入、跳过与手动确认都会留下记录，包括原值、写入值、来源与证据。" action={<Button size="sm" variant="ghost" onClick={() => void loadLogs()}><RefreshCw size={13} />刷新日志</Button>}>
        {logs.length === 0 ? <Empty title="暂无入库日志" text="运行章节分析、初始化或手动知识提案后，自动写入记录会显示在这里。" /> : <div className="space-y-3">
          {logs.map(log => <div key={log.id} className="rounded-md border border-line-soft bg-paper-raised p-4 text-body">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-semibold text-ink">{log.target_name || log.target_type}</div>
              <StatusBadge status={log.action} label={log.action === 'auto_applied' ? '自动入库' : log.action === 'auto_skipped' ? '自动跳过' : log.action === 'accepted' ? '手动接受' : log.action === 'rejected' ? '手动拒绝' : log.action} />
            </div>
            <MetaText className="mt-1">{log.operation} · {log.target_type} · {new Date(log.created_at).toLocaleString()} · {log.actor}</MetaText>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div><MetaText>原值</MetaText><pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-paper-sunken p-2 text-meta text-ink-soft">{valueText(log.old_value)}</pre></div>
              <div><MetaText>写入值</MetaText><pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-paper-sunken p-2 text-meta text-ink-soft">{valueText(log.new_value)}</pre></div>
            </div>
            {(log.reason || log.evidence) && <MetaText className="mt-2">{log.reason}{log.evidence && ` · 证据：${log.evidence}`}</MetaText>}
          </div>)}
        </div>}
      </Panel>}
    </div>
  </div>
}
