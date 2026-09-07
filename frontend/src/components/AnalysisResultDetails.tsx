import type { Analysis, AnalysisChange } from '../types'
import type { ReactNode } from 'react'
import { Badge, Button, MetaText, StatusBadge } from '../lib/ui'

type Item = Record<string, unknown>
type ChangeAction = (change: AnalysisChange, type: 'accept' | 'reject') => void

function asItem(value: unknown): Item {
  return typeof value === 'string' ? { text: value } : value && typeof value === 'object' && !Array.isArray(value) ? value as Item : {}
}

function asItems(value: unknown): Item[] {
  return Array.isArray(value) ? value.map(asItem) : []
}

function text(value: unknown, fallback = '未提供'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (Array.isArray(value)) return value.map(item => text(item, '')).filter(Boolean).join('、') || fallback
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function confidence(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : '未提供'
}

function JsonValue({ value }: { value: unknown }) {
  return <pre className="mt-1 whitespace-pre-wrap break-words rounded-md bg-paper-raised/70 p-2 text-meta leading-5 text-ink-soft">{typeof value === 'string' ? value || '未填写' : JSON.stringify(value ?? '', null, 2)}</pre>
}

function ResultSection({ title, count, children, empty }: { title: string; count: number; children: ReactNode; empty: string }) {
  return <section className="rounded-lg border border-line bg-paper-raised p-4">
    <div className="flex items-center justify-between">
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      <Badge tone={count > 0 ? 'accent' : 'neutral'}>{count} 条</Badge>
    </div>
    {count > 0 ? <div className="mt-3 space-y-2.5">{children}</div> : <p className="mt-3 text-body text-ink-faint">{empty}</p>}
  </section>
}

function CardGrid({ children }: { children: ReactNode }) {
  return <div className="mt-2 grid gap-1 text-meta text-ink-faint md:grid-cols-2">{children}</div>
}

function FactCards({ items }: { items: Item[] }) {
  return <>{items.map((item, index) => <div key={index} className="rounded-md bg-paper-sunken p-3 text-body">
    <div className="flex flex-wrap items-center gap-2"><Badge tone="accent">{text(item.fact_status, '待判断')}</Badge><span className="font-medium text-ink">{text(item.text, '未提供事实')}</span></div>
    <CardGrid><div>章节：{text(item.chapter)}</div><div>位置：{text(item.position)}</div><div>证据：{text(item.evidence)}</div><div>置信度：{confidence(item.confidence)}</div></CardGrid>
  </div>)}</>
}

function EventCards({ items }: { items: Item[] }) {
  return <>{items.map((item, index) => <div key={index} className="rounded-md bg-paper-sunken p-3 text-body">
    <div className="flex flex-wrap items-center gap-2"><span className="font-medium text-ink">{text(item.title, '未命名事件')}</span><Badge>{text(item.event_type, 'other')}</Badge><Badge tone="warn">{text(item.status, 'unresolved')}</Badge></div>
    <CardGrid><div>参与者：{text(item.participants)}</div><div>地点：{text(item.location)}</div><div>开始：{text(item.start_position)}</div><div>结束：{text(item.end_position, 'unknown')}</div><div>章节：{text(item.chapter || item.related_chapters)}</div><div>证据：{text(item.evidence)}</div></CardGrid>
    <MetaText className="mt-1">置信度：{confidence(item.confidence)} · 因果关系：{text(item.causal_relations)}</MetaText>
  </div>)}</>
}

function BeliefCards({ items }: { items: Item[] }) {
  return <>{items.map((item, index) => <div key={index} className="rounded-md bg-paper-sunken p-3 text-body">
    <div className="font-medium text-ink">{text(item.character)} 对 {text(item.subject)} 的认知</div>
    <div className="mt-1.5 text-ink-soft">{text(item.belief, '未提供认知内容')}</div>
    <CardGrid><div>类型：{text(item.belief_type)}</div><div>确定度：{confidence(item.certainty)}</div><div>章节：{text(item.chapter || item.source_chapter)}</div><div>证据：{text(item.evidence)}</div></CardGrid>
  </div>)}</>
}

function RelationCards({ items }: { items: Item[] }) {
  return <>{items.map((item, index) => <div key={index} className="rounded-md bg-paper-sunken p-3 text-body">
    <div className="font-medium text-ink">{text(item.from)} → {text(item.to)}</div>
    <div className="mt-2 space-y-2">{asItems(item.changes).map((change, changeIndex) => <div key={changeIndex} className="rounded-md border border-line bg-paper-raised p-2">
      <div className="flex flex-wrap items-center gap-2"><Badge tone="warn">{text(change.field)}</Badge><span className="font-medium">变化：{text(change.delta, '方向未判断')}</span><span className="text-meta text-ink-faint">置信度 {confidence(change.confidence)}</span></div>
      <p className="mt-1 text-meta text-ink-soft">原因：{text(change.reason)}</p>
      <p className="mt-1 text-meta text-ink-soft">证据：{text(change.evidence)}</p>
    </div>)}</div>
  </div>)}</>
}

function ForeshadowingCards({ items }: { items: Item[] }) {
  return <>{items.map((item, index) => <div key={index} className="rounded-md bg-paper-sunken p-3 text-body">
    <div className="flex flex-wrap items-center gap-2"><span className="font-medium text-ink">{text(item.title, '未命名伏笔候选')}</span><Badge tone="warn">候选 · {text(item.payoff_status || item.status, 'unresolved')}</Badge></div>
    <p className="mt-1.5 text-ink-soft">{text(item.expected_question, '模型未提供预期问题')}</p>
    <MetaText className="mt-1.5">埋下章节：{text(item.seed_chapter)} · 证据：{text(item.seed_evidence)} · 可能回收：{text(item.possible_payoff)}</MetaText>
  </div>)}</>
}

function ChangeCards({ changes, onAction }: { changes: AnalysisChange[]; onAction?: ChangeAction }) {
  if (changes.length === 0) return <p className="mt-3 text-body text-ink-faint">本次没有生成可直接写入资料库的变更。事实和角色认知会保留在分析记录中；带有明确标题的事件可自动进入事件轴，无法结构化的内容不会强行写入。</p>
  return <div className="mt-3 space-y-3">{changes.map(change => <div key={change.id} className="rounded-md border border-warn/40 bg-warn-soft/60 p-3 text-body">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="font-medium text-ink">{change.target_name || change.change_type}</div>
      <StatusBadge status={change.status} />
    </div>
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      <div><MetaText>原值</MetaText><JsonValue value={change.old_value} /></div>
      <div><MetaText>建议新值</MetaText><JsonValue value={change.proposed_value} /></div>
    </div>
    <p className="mt-2 text-ink-soft">修改原因：{change.reason || '未提供'}</p>
    <MetaText className="mt-1">正文证据：{change.evidence || '未提供'} · 置信度：{confidence(change.confidence)}{change.conflict ? ' · 存在冲突' : ''}</MetaText>
    {change.status === 'pending' && onAction && <div className="mt-3 flex flex-wrap gap-2">
      <Button size="sm" variant="primary" onClick={() => onAction(change, 'accept')}>接受写入</Button>
      <Button size="sm" variant="danger" onClick={() => onAction(change, 'reject')}>拒绝写入</Button>
    </div>}
  </div>)}</div>
}

function CharacterCards({ items }: { items: Item[] }) {
  const labels: Record<string, string> = { summary: '简介', appearance: '外貌', personality: '性格', level: '等级 / 境界', inventory: '背包 / 持有物', relationships: '与他人的关系', role: '角色定位', age: '年龄', identity: '身份', goals: '目标', fears: '恐惧', abilities: '能力', secrets: '秘密', current_state: '当前状态' }
  const fields = Object.keys(labels)
  return <>{items.map((item, index) => {
    const changes = asItems(item.field_changes)
    const evidence = text(item.evidence, '模型未单独提供正文证据')
    return <div key={index} className="rounded-md bg-paper-sunken p-4 text-body">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold text-ink">{text(item.name, '未命名角色')}</span>
        <Badge tone={item.match_status === 'new' ? 'warn' : 'accent'}>{item.match_status === 'new' ? '建议新增' : '已有角色'}</Badge>
        <MetaText>置信度：{confidence(item.confidence)}</MetaText>
      </div>
      <div className="mt-3 grid gap-2.5 md:grid-cols-2">{fields.filter(field => item[field] !== undefined && item[field] !== null && item[field] !== '').map(field => <div key={field} className="rounded-md border border-line bg-paper-raised p-2.5">
        <MetaText>{labels[field]}</MetaText>
        <div className="mt-0.5 whitespace-pre-wrap break-words text-ink-soft">{text(item[field])}</div>
      </div>)}</div>
      {changes.length > 0 && <div className="mt-3 rounded-md border border-warn/40 bg-warn-soft/60 p-3">
        <div className="font-medium text-warn-strong">与角色库的差异</div>
        <div className="mt-2 space-y-2">{changes.map((change, changeIndex) => <div key={changeIndex} className="grid gap-2 text-meta md:grid-cols-3">
          <span className="font-medium text-ink-soft">{text(change.label || change.field)}</span>
          <span>原值：{text(change.old_value, '空')}</span>
          <span>建议：{text(change.new_value, '空')}</span>
        </div>)}</div>
      </div>}
      <MetaText className="mt-3">正文证据：{evidence}</MetaText>
    </div>
  })}</>
}

export function AnalysisResultDetails({ analysis, onChangeAction }: { analysis: Analysis; onChangeAction?: ChangeAction }) {
  const result = analysis.result_json || {}
  if (analysis.status === 'failed') return <section className="rounded-lg border border-danger/25 bg-danger-soft p-4 text-body text-danger-strong">
    <h3 className="font-semibold">分析失败</h3>
    <p className="mt-2">{analysis.error_message || 'LLM 没有返回可识别的分析结果。'}</p>
    {analysis.raw_response && <details className="mt-3"><summary className="cursor-pointer text-meta underline underline-offset-2">查看原始响应</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-paper-raised/70 p-3 text-meta scrollbar-thin">{analysis.raw_response}</pre></details>}
  </section>
  const characterItems = asItems(result.characters)
  const facts = asItems(result.facts)
  const events = asItems(result.events)
  const beliefs = asItems(result.beliefs)
  const relationChanges = asItems(result.relation_changes)
  const foreshadowing = asItems(result.foreshadowing_candidates)
  const applyNote = <p className="mt-1 text-body text-warn-strong">当前设置为自动入库：无冲突的变更已直接写入并生成日志；冲突或无法结构化的内容不会强行写入。历史待确认记录仍可在这里处理。</p>
  if (result.analysis_type === 'characters') return <div className="space-y-4">
    <ResultSection title="本章出现的角色" count={characterItems.length} empty="本章没有识别到带姓名且有明确证据的角色。"><CharacterCards items={characterItems} /></ResultSection>
    <section className="rounded-lg border border-warn/40 bg-warn-soft/60 p-4">
      <div className="flex items-center justify-between"><h3 className="text-[15px] font-semibold text-warn-strong">角色卡入库结果</h3><Badge tone={analysis.changes.length > 0 ? 'warn' : 'neutral'}>{analysis.changes.length} 条</Badge></div>
      {applyNote}
      <ChangeCards changes={analysis.changes} onAction={onChangeAction} />
    </section>
  </div>
  return <div className="space-y-4">
    <ResultSection title="事实候选" count={facts.length} empty="本次没有抽取到事实候选。"><FactCards items={facts} /></ResultSection>
    <ResultSection title="事件候选" count={events.length} empty="本次没有抽取到事件候选。"><EventCards items={events} /></ResultSection>
    <ResultSection title="角色认知" count={beliefs.length} empty="本次没有抽取到角色认知变化。"><BeliefCards items={beliefs} /></ResultSection>
    <ResultSection title="关系变化" count={relationChanges.length} empty="本次没有抽取到关系变化。"><RelationCards items={relationChanges} /></ResultSection>
    <ResultSection title="伏笔候选" count={foreshadowing.length} empty="本次没有识别到伏笔候选。"><ForeshadowingCards items={foreshadowing} /></ResultSection>
    <section className="rounded-lg border border-warn/40 bg-warn-soft/60 p-4">
      <div className="flex items-center justify-between"><h3 className="text-[15px] font-semibold text-warn-strong">资料库入库结果</h3><Badge tone={analysis.changes.length > 0 ? 'warn' : 'neutral'}>{analysis.changes.length} 条</Badge></div>
      {applyNote}
      <ChangeCards changes={analysis.changes} onAction={onChangeAction} />
    </section>
  </div>
}
