import { useEffect, useRef, useState } from 'react'
import { ClipboardCheck, Loader2 } from 'lucide-react'
import { api } from '../api/client'
import type { Chapter } from '../types'
import { Badge, Button, Field, InlineError, MetaText, Modal, Textarea, toast } from './ui'
import { TextContent } from './TextContent'
import { opinionMarkdown, opinionTone, splitOpinions, type Opinion } from './reviewOpinions'

type ReviewJob = {id: string; book_id: number; chapter_id: number; kind: 'review' | 'revision'; base_version_id: number;
  status: string; message: string; opinions: string; saved_version_id: number | null; check_report: string;
  has_output: boolean; node_progress?: {label: string; status: string}[]}
const active = (job: ReviewJob | null) => !!job && ['queued', 'preparing', 'submitting', 'running'].includes(job.status)

export function ChapterReview({chapter, disabled, onBusy}: {chapter: Chapter; disabled: boolean; onBusy: (busy: boolean) => void}) {
  const [job, setJob] = useState<ReviewJob | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [items, setItems] = useState<Opinion[]>([])
  const [decisions, setDecisions] = useState('')
  const [raw, setRaw] = useState<string | null>(null)
  const [editingOpinions, setEditingOpinions] = useState(false)
  const seen = useRef(new Set<string>())
  useEffect(() => { setEditingOpinions(false) }, [open, job?.id])
  const draftKey = (id: string) => `noveltools:review-opinions:${id}`
  const receive = (j: ReviewJob | null) => {
    setJob(j)
    if (j?.opinions && !seen.current.has(j.id)) {
      seen.current.add(j.id)
      let text = j.opinions, supplement = ''
      try {
        const saved = JSON.parse(localStorage.getItem(draftKey(j.id)) || 'null')
        if (typeof saved?.text === 'string') text = saved.text
        if (typeof saved?.decisions === 'string') supplement = saved.decisions
      } catch { /* Invalid local notes never replace the server's editorial report. */ }
      setItems(splitOpinions(text)); setDecisions(supplement)
    }
  }
  useEffect(() => {
    let alive = true
    void api<ReviewJob | null>(`/chapters/${chapter.id}/review/latest`).then(j => { if (alive) receive(j) })
      .catch(e => { if (alive) setError(String(e)) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [chapter.id])
  useEffect(() => { onBusy(busy || active(job)); return () => onBusy(false) }, [busy, job?.status, onBusy])
  useEffect(() => {
    if (!job || !active(job)) return
    let alive = true, timer: number
    const poll = async () => {
      try {
        const fresh = await api<ReviewJob>(`/chapter-review/jobs/${job.id}`)
        if (!alive) return
        receive(fresh); setError('')
        if (fresh.saved_version_id && !seen.current.has(`saved:${fresh.saved_version_id}`)) {
          seen.current.add(`saved:${fresh.saved_version_id}`)
          window.dispatchEvent(new CustomEvent('canvas-prose-saved', {detail: {chapterId: fresh.chapter_id, bookId: fresh.book_id}}))
          toast('修订已保存为新版本，本轮审核结束')
        }
        if (active(fresh)) timer = window.setTimeout(poll, 1500)
      } catch (e) {
        if (alive) { setError(`读取进度失败：${String(e)}。正在重查原任务，不会重复提交。`); timer = window.setTimeout(poll, 5000) }
      }
    }
    timer = window.setTimeout(poll, 600)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [job?.id, job?.status])
  const remember = (next: Opinion[], extra = decisions) => {
    if (!job) return
    try { localStorage.setItem(draftKey(job.id), JSON.stringify({text: next.map(i => i.text).join(''), decisions: extra})) }
    catch { setError('意见未能保存在浏览器中，请复制备份后再关闭。') }
  }
  const start = async (kind: 'review' | 'revision') => {
    if (busy || active(job)) return
    setBusy(true); setOpen(true); setError(''); setRaw(null)
    try {
      const result = await api<ReviewJob>(`/chapters/${chapter.id}/${kind}`, {method: 'POST', body: JSON.stringify({
        base_version_id: chapter.current_version_id,
        ...(kind === 'revision' ? {review_job_id: job?.id, opinions: items.map(i => i.text).join(''), decisions} : {})})})
      receive(result)
      if (kind === 'review') { setItems([]); setDecisions('') }
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const outdated = !!job && job.base_version_id !== chapter.current_version_id
  const canRevise = job?.kind === 'review' && job.status === 'completed' && !outdated && items.some(i => i.text.trim())
  const running = busy || active(job)
  const failure = error || (job && ['failed', 'conflict', 'cancelled'].includes(job.status) ? job.message : '')
  return <>
    <Button size="sm" disabled={disabled || busy || loading || !chapter.word_count} title="只审核当前这一章" onClick={() => active(job) ? setOpen(true) : void start('review')}>
      {running ? <Loader2 size={14} className="animate-spin"/> : <ClipboardCheck size={14}/>} {active(job) ? '审核进度' : '审核'}
    </Button>
    {(job || error) && <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>审核意见</Button>}
    <Modal open={open} onClose={() => setOpen(false)} title={`第${chapter.chapter_number}章 · 主编意见`} width="max-w-5xl" footer={<div className="w-full min-w-0 space-y-2">
      {failure && <div className="max-h-32 overflow-y-auto" aria-label="审核修订操作提示"><InlineError message={failure}/></div>}
      <div className="flex flex-wrap items-center justify-end gap-2">
      <Button variant="ghost" onClick={() => setOpen(false)}>关闭</Button>
      {active(job) && <Button onClick={() => void api<ReviewJob>(`/workflow-writing/jobs/${job!.id}/cancel`, {method: 'POST'}).then(j => setJob(old => old && ({...old, ...j}))).catch(e => setError(String(e)))}>停止任务</Button>}
      {!running && <Button disabled={disabled || !chapter.word_count} onClick={() => void start('review')}>重新审核本章</Button>}
      <Button variant="primary" disabled={disabled || running || !canRevise} onClick={() => void start('revision')}>{job?.kind === 'revision' && active(job) ? '正在修订…' : '修订'}</Button>
      </div>
    </div>}>
      <div className="space-y-4">
        <MetaText>以第{chapter.chapter_number}章已保存正文为基线，结合审核意见、章纲、前文和写作规则全文修订。完成后保存新的草稿版本，原稿保留在历史中；需要再次审核时手动启动。</MetaText>
        {job && <div role="status" className="rounded-md bg-paper-sunken px-3 py-2 text-body">{job.message}</div>}
        {outdated && job?.kind === 'review' && <InlineError message="正文已变化，这份意见只能查看。请重新审核当前版本后再修订。"/>}
        {running && <div className="space-y-1" aria-live="polite"><p className="flex items-center gap-2 text-body"><Loader2 size={15} className="animate-spin"/>{busy ? '正在提交…' : '工作流执行中，可关闭弹窗，任务会继续。'}</p>{job?.node_progress?.map((n, i) => <div key={i} className="flex justify-between text-meta"><span>{n.label}</span><span>{{SUCCEEDED:'完成', RUNNING:'运行中', WAITING:'等待返回', READY:'就绪', PENDING:'等待', FAILED:'失败', CANCELLED:'已停止'}[n.status] || n.status}</span></div>)}</div>}
        {!!items.length && <>
          <div className="flex flex-wrap items-center gap-2"><Badge tone="danger">M 确定问题</Badge><Badge tone="warn">M 可选建议 / Q 待决定</Badge><Badge tone="accent">K 保护项</Badge><Button size="sm" variant="ghost" onClick={() => setEditingOpinions(!editingOpinions)}>{editingOpinions ? '返回排版阅读' : '编辑原文'}</Button></div>
          {items.map((item, index) => <section key={`${job?.id}:${index}`} className={`rounded-md border p-3 ${opinionTone(item)}`}>
            <div className="mb-2 flex items-center gap-2 text-body font-medium"><span>{item.key}</span>{item.kind !== 'intro' && <span className="text-meta font-normal">{item.kind === 'K' ? '保护项' : item.kind === 'Q' ? '待作者决定' : /确定问题/.test(item.text) ? '确定问题' : '修改建议'}</span>}</div>
            {editingOpinions ? <Textarea aria-label={`审核条目 ${item.key}`} className="bg-paper-raised/80 text-[0.8125rem] !leading-[1.65]" rows={2} style={{minHeight: 64, maxHeight: 520, fieldSizing: 'content'} as React.CSSProperties} disabled={running || job?.kind === 'revision'} value={item.text} onChange={e => {const next = items.map((x, i) => i === index ? {...x, text: e.target.value} : x); setItems(next); remember(next)}}/> : <TextContent label={`审核条目 ${item.key}`} text={opinionMarkdown(item.text)} copyText={item.text} format="markdown" className="text-body"/>}
          </section>)}
          <Field label="采纳决定与补充要求（可留空）" hint="留空时默认修正确定问题，可选建议按收益判断；未裁定的Q项保持原文。">
            <Textarea aria-label="采纳决定与补充要求" disabled={running || job?.kind === 'revision'} value={decisions} onChange={e => {setDecisions(e.target.value); remember(items, e.target.value)}} rows={3}/>
          </Field>
        </>}
        {job?.check_report && <details><summary className="cursor-pointer text-accent-700">查看修订工作流附带的复核报告</summary><TextContent label="修订复核报告" text={opinionMarkdown(job.check_report)} copyText={job.check_report} format="markdown" className="mt-3 text-body"/></details>}
        {job?.has_output && job.kind === 'revision' && <Button size="sm" onClick={() => void api<{text: string}>(`/workflow-writing/jobs/${job.id}/output`).then(r => setRaw(r.text)).catch(e => setError(String(e)))}>查看修订输出</Button>}
        {raw !== null && <TextContent label="修订输出" text={raw} className="paper-text"/>}
      </div>
    </Modal>
  </>
}
