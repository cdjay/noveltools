import { useEffect, useState } from 'react'
import { ClipboardList, Sparkles } from 'lucide-react'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import type { Analysis, Chapter } from '../types'
import { Badge, Button, Empty, InlineError, Loading, MetaText, PageHeader, Panel, Stat, StatusBadge } from '../lib/ui'
import { AnalysisResultDetails } from '../components/AnalysisResultDetails'

type Issue = {
  severity?: string
  category?: string
  message?: string
  title?: string
  current_text?: string
  reference?: string
  evidence?: string
  suggestion?: string
  confidence?: number
  review_status?: string
}

const issueStatusLabels: Record<string, string> = { pending: '待判断', confirmed: '已确认存在', dismissed: '已排除', deferred: '稍后处理' }
const analysisTypeLabels: Record<string, string> = { characters: '角色分析', chapter_summary: '章节梗概', review: '章节审阅', update: '资料更新', graphify_init: '初始化' }
const analysisTypeLabel = (type?: string) => analysisTypeLabels[type || ''] || '章节分析'

function normalizeIssue(value: unknown): Issue {
  if (typeof value === 'string') return { message: value }
  if (value && typeof value === 'object') return value as Issue
  return { message: '模型没有返回问题描述' }
}

function IssueCard({ issue, index, analysis, onReview }: { issue: Issue; index: number; analysis: Analysis; onReview: (analysis: Analysis, index: number, status: string) => void }) {
  const status = issue.review_status || 'pending'
  const severity = issue.severity === 'error' ? '明确冲突' : issue.severity === 'warning' ? '高度可疑' : '建议确认'
  const message = issue.message || issue.title || '模型没有返回问题描述'
  return <div className="rounded-md border border-danger/25 bg-paper-raised p-4 shadow-card">
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-meta font-medium tracking-wide text-danger-strong">{severity} · {issue.category || '连续性'}</div>
        <div className="mt-1 font-semibold text-ink">{message}</div>
      </div>
      <Badge tone={status === 'confirmed' ? 'danger' : status === 'dismissed' ? 'neutral' : status === 'deferred' ? 'accent' : 'warn'}>{issueStatusLabels[status] || status}</Badge>
    </div>
    {issue.current_text && <div className="mt-3 rounded-md bg-danger-soft p-3 text-body"><MetaText className="!text-danger-strong">当前正文</MetaText><div className="mt-1 text-danger-strong">{issue.current_text}</div></div>}
    {issue.reference && <div className="mt-3 rounded-md bg-paper-sunken p-3 text-body"><MetaText>参考依据</MetaText><div className="mt-1 text-ink-soft">{issue.reference}</div></div>}
    {issue.evidence && <div className="mt-3 text-body text-ink-soft"><span className="font-medium text-ink">证据：</span>{issue.evidence}</div>}
    {issue.suggestion && <div className="mt-2 text-body text-ink-soft"><span className="font-medium text-ink">建议动作：</span>{issue.suggestion}</div>}
    <MetaText className="mt-3">置信度：{typeof issue.confidence === 'number' ? `${Math.round(issue.confidence * 100)}%` : '未提供'}</MetaText>
    {status === 'pending' && <div className="mt-4 flex flex-wrap gap-2">
      <Button size="sm" variant="primary" onClick={() => onReview(analysis, index, 'confirmed')}>确认存在问题</Button>
      <Button size="sm" variant="secondary" onClick={() => onReview(analysis, index, 'dismissed')}>不是问题</Button>
      <Button size="sm" variant="ghost" className="text-warn-strong" onClick={() => onReview(analysis, index, 'deferred')}>稍后处理</Button>
    </div>}
  </div>
}

export function AnalysisPage() {
  const { chapters } = useAppStore()
  const [selectedChapter, setSelectedChapter] = useState<Chapter | null>(chapters[0] || null)
  const [records, setRecords] = useState<Analysis[]>([])
  const [selected, setSelected] = useState<Analysis | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    if (!selectedChapter) return
    setLoading(true); setError('')
    try { setRecords(await api<Analysis[]>(`/chapters/${selectedChapter.id}/analysis`)) } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { setSelected(null); void load() }, [selectedChapter?.id])

  const analyze = async () => {
    if (!selectedChapter) return
    setBusy(true); setError('')
    try {
      const result = await api<Analysis>(`/chapters/${selectedChapter.id}/analyze`, { method: 'POST', body: JSON.stringify({ analysis_type: 'characters' }) })
      setRecords(previous => [result, ...previous]); setSelected(result)
    } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }

  const refreshAnalysis = async (analysis: Analysis) => {
    const fresh = await api<Analysis>(`/analysis/${analysis.id}`)
    setSelected(fresh)
    setRecords(previous => previous.map(item => item.id === fresh.id ? fresh : item))
  }
  const action = async (analysis: Analysis, changeId: number, type: 'accept' | 'reject') => {
    await api(`/analysis/${analysis.id}/changes/${changeId}/${type}`, { method: 'POST' })
    await refreshAnalysis(analysis)
  }
  const reviewIssue = async (analysis: Analysis, issueIndex: number, status: string) => {
    const fresh = await api<Analysis>(`/analysis/${analysis.id}/issues/${issueIndex}/review`, { method: 'POST', body: JSON.stringify({ status }) })
    setSelected(fresh)
    setRecords(previous => previous.map(item => item.id === fresh.id ? fresh : item))
  }

  const issues = (selected?.result_json?.issues || []).map(normalizeIssue)
  const characterAnalysis = selected?.result_json?.analysis_type === 'characters'

  return <div className="p-6">
    <PageHeader title="分析记录" description="每一次分析的原始响应、结构化结果和作者确认状态都会保留，可以随时追溯。" />
    <div className="mt-5 space-y-4">
      {error && <InlineError message={error} />}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Panel title="选择章节" className="self-start" bodyClassName="max-h-[70vh] overflow-y-auto scrollbar-thin">
          {chapters.length === 0 ? <Empty compact title="还没有章节" text="先在阅读器中创建章节。" /> : <div className="space-y-1">
            {chapters.map(chapter => <button key={chapter.id} className={`w-full rounded-md border-l-2 px-3 py-2.5 text-left transition-colors duration-fast ${selectedChapter?.id === chapter.id ? 'border-accent-600 bg-accent-50' : 'border-transparent hover:bg-paper-sunken'}`} onClick={() => setSelectedChapter(chapter)}>
              <MetaText>第 {chapter.chapter_number} 章</MetaText>
              <div className="mt-0.5 truncate font-medium text-ink">{chapter.title}</div>
            </button>)}
          </div>}
        </Panel>
        <div className="space-y-4">
          <Panel title={selectedChapter ? `第 ${selectedChapter.chapter_number} 章「${selectedChapter.title}」` : '分析'} description="手动角色分析会追加一条新记录，不会覆盖历史。"
            action={<Button variant="primary" disabled={!selectedChapter || busy} onClick={() => void analyze()}><Sparkles size={14} />{busy ? '分析中…' : '分析本章角色'}</Button>}>
            {loading ? <Loading /> : records.length === 0 ? <Empty icon={<ClipboardList size={20} />} title="暂无分析记录" text="运行一次分析，或等待保存正文后的自动分析。" /> : <div className="space-y-1.5">
              {records.map(record => <button key={record.id} className={`w-full rounded-md border px-4 py-3 text-left transition-colors duration-fast ${selected?.id === record.id ? 'border-accent-300 bg-accent-50/60' : 'border-line-soft hover:bg-paper-sunken'}`} onClick={() => setSelected(record)}>
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium text-ink">分析 #{record.id}<span className="ml-2 text-meta font-normal text-ink-faint">{analysisTypeLabel(record.result_json?.analysis_type)}</span></div>
                  <StatusBadge status={record.status} />
                </div>
                <MetaText className="mt-1">模型：{record.model} · 耗时 {record.elapsed_ms}ms · {new Date(record.created_at).toLocaleString()}</MetaText>
              </button>)}
            </div>}
          </Panel>
          {selected && <Panel title={`分析 #${selected.id} 结果`}>
            {characterAnalysis
              ? <div className="grid grid-cols-2 gap-3">
                <Stat label="本章出现角色" value={selected.result_json?.characters?.length || 0} />
                <Stat label="待确认角色变更" value={selected.changes.length} tone={selected.changes.length ? 'warn' : 'default'} />
              </div>
              : <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                {[['硬伤', 'issues'], ['事实', 'facts'], ['事件', 'events'], ['认知', 'beliefs'], ['关系变化', 'relation_changes']].map(([label, key]) => <Stat key={key} label={label} value={(selected.result_json as any)?.[key]?.length || 0} tone={key === 'issues' && (selected.result_json as any)?.[key]?.length ? 'danger' : 'default'} />)}
              </div>}
            {selected.status === 'failed' && <div className="mt-4"><InlineError message={`分析失败：${selected.error_message || '未返回具体错误，请检查后端日志。'}`} /></div>}
            <div className="mt-6 space-y-4">
              {!characterAnalysis && <>
                <div className="rounded-md border border-danger/25 bg-danger-soft p-4">
                  <h3 className="font-semibold text-danger-strong">硬伤检查：作者判断区</h3>
                  <p className="mt-1 text-body text-danger-strong/90">这里展示的是基于正文证据的疑点。你要确认的是“这个问题是否真实存在”，确认与否都不会自动修改正文或资料库。</p>
                </div>
                {issues.length === 0
                  ? <div className="rounded-md border border-line bg-paper-sunken p-4 text-body text-ink-soft">本次没有发现可供确认的硬伤疑点。这不等于绝对没有问题，只表示模型没有给出带证据的疑点。</div>
                  : issues.map((issue, index) => <IssueCard key={index} issue={issue} index={index} analysis={selected} onReview={reviewIssue} />)}
              </>}
              <AnalysisResultDetails analysis={selected} onChangeAction={(change, type) => action(selected, change.id, type)} />
              {selected.result_json?.notice && <div className="rounded-md bg-paper-sunken p-4 text-body text-ink-soft">{selected.result_json.notice}</div>}
            </div>
          </Panel>}
        </div>
      </div>
    </div>
  </div>
}
