import { useEffect, useRef, useState } from 'react'
import { create } from 'zustand'
import { GripHorizontal, Minus, Plus, Workflow, X } from 'lucide-react'
import { api } from '../api/client'
import type { Chapter } from '../types'
import { Button, Field, InlineError, MetaText, Modal, Select, toast } from './ui'
import { TextContent } from './TextContent'

type Job = { id: string; book_id: number; chapter_id: number; status: string; message: string; workflow_id: string; run_id: string | null; saved_version_id: number | null; has_output: boolean; logs?: {id: number; time: string; text: string}[]; node_progress?: {label: string; status: string}[] }
const active = (j: Job | null) => !!j && ['queued', 'preparing', 'submitting', 'running', 'archiving'].includes(j.status)
const useWindow = create<{job: Job | null; open: boolean; busy: boolean; error: string}>(() => ({job: null, open: false, busy: false, error: ''}))
const remember = (job: Job) => { try { localStorage.setItem('canvas-writing-job', job.id) } catch { /* Optional recovery hint. */ } }

export function CanvasWritingButton({ chapter, disabled }: { chapter: Chapter; disabled: boolean }) {
  const state = useWindow()
  const begin = async () => {
    if (active(state.job)) { useWindow.setState({open: true}); return }
    if (chapter.word_count > 0 && !window.confirm('本章已有正文。工作流将生成并保存新版本，原正文保留在历史中，是否继续？')) return
    useWindow.setState({busy: true, error: '', open: true, job: null})
    try {
      const job = await api<Job>(`/chapters/${chapter.id}/workflow-writing`, {method: 'POST', body: JSON.stringify({base_version_id: chapter.current_version_id, replace_existing: chapter.word_count > 0})})
      remember(job); useWindow.setState({job})
    } catch (e) { useWindow.setState({error: String(e)}) }
    finally { useWindow.setState({busy: false}) }
  }
  return <Button size="sm" disabled={disabled || state.busy} onClick={() => void begin()}><Workflow size={14}/>{active(state.job) ? '查看工作流进度' : '工作流生成正文'}</Button>
}

type WorkflowSelection = {workflow_id: string; output_node_id: string; output_port: string; revision: number}
type OutputChoice = {node_id: string; label: string; port: string}

export function WorkflowSettings({bookId, disabled}: {bookId: number; disabled: boolean}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loadingOutputs, setLoadingOutputs] = useState(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState<{id: string; name: string}[]>([])
  const [selection, setSelection] = useState<WorkflowSelection | null>(null)
  const [outputs, setOutputs] = useState<OutputChoice[]>([])
  useEffect(() => {
    if (!open) return
    let alive = true
    setBusy(true); setError(''); setSelection(null); setOutputs([])
    void Promise.all([api<{id: string; name: string}[]>('/canvas-writing/workflows'),
      api<WorkflowSelection>(`/books/${bookId}/workflow-writing/settings`)]).then(([list, selected]) => {
      if (alive) { setItems(list); setSelection(selected) }
    }).catch(e => { if (alive) setError(String(e)) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [open, bookId])
  useEffect(() => {
    if (!open || !selection?.workflow_id) return
    let alive = true
    setLoadingOutputs(true); setOutputs([])
    void api<OutputChoice[]>(`/canvas-writing/workflows/${encodeURIComponent(selection.workflow_id)}/outputs`).then(list => {
      if (alive) setOutputs(list)
    }).catch(e => { if (alive) setError(String(e)) }).finally(() => { if (alive) setLoadingOutputs(false) })
    return () => { alive = false }
  }, [open, selection?.workflow_id])
  const save = async () => {
    if (!selection) return
    setBusy(true); setError('')
    try {
      await api(`/books/${bookId}/workflow-writing/settings`, {method: 'PUT', body: JSON.stringify({
        workflow_id: selection.workflow_id, output_node_id: selection.output_node_id,
        output_port: selection.output_port, base_revision: selection.revision})})
      setOpen(false); toast('已保存本书的正文工作流与输出选择')
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const valid = selection && outputs.some(o => o.node_id === selection.output_node_id && o.port === selection.output_port)
  return <><Button size="sm" disabled={disabled} onClick={() => setOpen(true)}>工作流设置</Button>
    <Modal open={open} onClose={() => {if (!busy) setOpen(false)}} title="本书正文工作流" footer={<Button disabled={busy || loadingOutputs || !valid} onClick={() => void save()}>保存工作流设置</Button>}>
      {error && <InlineError message={error}/>}
      <div className="space-y-4">
        <MetaText>先在画布搭建并保存工作流，再选择要回填正文的文本输出。本书会记住选择，节点名称可自由修改。</MetaText>
        <Field label="工作流"><Select aria-label="正文工作流选择" disabled={busy} value={selection?.workflow_id || ''} onChange={e => setSelection(s => s && ({...s, workflow_id: e.target.value, output_node_id: '', output_port: 'text'}))}>
          <option value="">请选择已保存的工作流</option>{items.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
        </Select></Field>
        <Field label="回填正文的输出"><Select aria-label="正文输出选择" disabled={busy || loadingOutputs} value={valid ? JSON.stringify([selection.output_node_id, selection.output_port]) : ''} onChange={e => {
          if (!e.target.value) return
          const [node, port] = JSON.parse(e.target.value) as [string, string]
          setSelection(s => s && ({...s, output_node_id: node, output_port: port}))
        }}><option value="">{loadingOutputs ? '正在读取节点…' : '请选择文本输出'}</option>{outputs.map(o => <option key={JSON.stringify([o.node_id,o.port])} value={JSON.stringify([o.node_id,o.port])}>{o.label} · {o.port}</option>)}</Select></Field>
        <MetaText>正文页调用时向提取节点传入当前书籍与目标章节。画布独立运行只显示结果，不会回填正文。输出首行写章节名，其余为正文。</MetaText>
        <a className="text-accent-700" href={`http://localhost:3010/${selection ? `?workflow=${encodeURIComponent(selection.workflow_id)}` : ''}`} target="_blank" rel="noreferrer">打开画布编辑工作流</a>
      </div>
    </Modal></>
}

export function CanvasWritingHost() {
  const {job, open, busy, error} = useWindow()
  const [collapsed, setCollapsed] = useState(false)
  const [position, setPosition] = useState<{left: number; top: number} | null>(null)
  const [output, setOutput] = useState<string | null>(null)
  const logPanel = useRef<HTMLDivElement>(null)
  const followLog = useRef(true)
  useEffect(() => {
    if (followLog.current && logPanel.current) logPanel.current.scrollTop = logPanel.current.scrollHeight
  }, [job?.id, job?.logs?.[job.logs.length - 1]?.id, collapsed, open])
  useEffect(() => { setOutput(null); followLog.current = true }, [job?.id])
  const seen = useRef(new Set<string>())
  useEffect(() => { if (busy || error) setCollapsed(false) }, [busy, error])
  useEffect(() => {
    let alive = true
    const restore = async () => {
      try {
        const id = localStorage.getItem('canvas-writing-job')
        if (id) {
          const j = await api<Job>(`/workflow-writing/jobs/${id}`)
          if (alive && !useWindow.getState().busy && !useWindow.getState().job) useWindow.setState({job: j, open: true})
        }
      } catch { /* A removed historical job does not block writing. */ }
    }
    void restore(); return () => { alive = false }
  }, [])
  useEffect(() => {
    if (!job || !active(job)) return
    let alive = true, timer: number
    const poll = async () => {
      try {
        const fresh = await api<Job>(`/workflow-writing/jobs/${job.id}`)
        if (!alive) return
        useWindow.setState({job: fresh, error: ''})
        const changeKey = `${fresh.saved_version_id}`
        if (fresh.saved_version_id && !seen.current.has(changeKey)) {
          seen.current.add(changeKey)
          window.dispatchEvent(new CustomEvent('canvas-prose-saved', {detail: {chapterId: fresh.chapter_id, bookId: fresh.book_id}}))
          toast('工作流正文已保存，请审阅修改')
        }
        if (active(fresh)) timer = window.setTimeout(poll, 2000)
      } catch (e) { if (alive) { useWindow.setState({error: String(e)}); timer = window.setTimeout(poll, 5000) } }
    }
    timer = window.setTimeout(poll, 1000)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [job?.id, job?.status])
  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('button')) return
    const box = e.currentTarget.parentElement!.getBoundingClientRect(), startX = e.clientX, startY = e.clientY
    e.currentTarget.setPointerCapture(e.pointerId)
    const move = (event: PointerEvent) => setPosition({left: Math.max(0, Math.min(window.innerWidth - box.width, box.left + event.clientX - startX)), top: Math.max(0, Math.min(window.innerHeight - 100, box.top + event.clientY - startY))})
    const target = e.currentTarget
    const end = () => { target.removeEventListener('pointermove', move); target.removeEventListener('pointerup', end); target.removeEventListener('pointercancel', end) }
    target.addEventListener('pointermove', move); target.addEventListener('pointerup', end); target.addEventListener('pointercancel', end)
  }
  const progress = !job ? 0 : ({queued: 5, preparing: 20, submitting: 35, running: 55, archiving: 85, completed: 100}[job.status] ?? (job.saved_version_id ? 85 : 0))
  if (!open) return null
  return <>
    <aside aria-label="画布正文工作流" className="fixed z-50 flex max-h-[calc(100dvh-40px)] w-[420px] max-w-[calc(100vw-40px)] flex-col overflow-hidden rounded-lg border border-line bg-paper-raised shadow-xl" style={position ? {left: position.left, top: position.top} : {right: 20, bottom: 20}}>
      <div className="flex shrink-0 cursor-move touch-none items-center gap-2 border-b border-line px-3 py-2" onPointerDown={drag}><GripHorizontal size={15}/><span className="flex-1 text-body font-medium">正文工作流</span><Button size="sm" variant="ghost" aria-label={collapsed ? '展开工作流进度' : '折叠工作流进度'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <Plus size={14}/> : <Minus size={14}/>}</Button>{!busy && <Button size="sm" variant="ghost" aria-label="关闭工作流进度" onClick={() => useWindow.setState({open: false})}><X size={14}/></Button>}</div>
      <div className="min-h-0 overflow-y-auto p-3"><p className="text-meta text-ink-soft">{busy ? '正在提交任务…' : error && !job ? '任务未启动，请修正下方配置问题' : job?.message || '准备调用画布'}</p><div role="progressbar" aria-label="画布执行阶段" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100} className="mt-2 h-1.5 overflow-hidden rounded bg-paper-sunken"><div className="h-full bg-accent-600 transition-all" style={{width: `${progress}%`}}/></div>
        {error && <div className="mt-2"><InlineError message={error}/></div>}
        {!collapsed && <div className="mt-3 space-y-3">
          <section aria-label="运行日志">
            <div className="mb-2 flex items-center justify-between"><span className="text-meta font-medium">运行日志</span><MetaText>最近 100 条 · 自动保存</MetaText></div>
            <div ref={logPanel} role="log" aria-label="工作流运行日志" tabIndex={0}
              onScroll={e => { const el = e.currentTarget; followLog.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24 }}
              className="max-h-56 overflow-y-auto rounded-md border border-line-soft bg-paper p-2 text-meta">
              {job?.logs?.length ? job.logs.map(entry => <div key={entry.id} className="flex gap-2 py-1">
                <time className="shrink-0 tabular-nums text-ink-faint" dateTime={entry.time}>{new Date(entry.time).toLocaleTimeString('zh-CN', {hour12: false})}</time>
                <span className="min-w-0 whitespace-pre-wrap break-words text-ink-soft">{entry.text}</span>
              </div>) : <MetaText>{job ? '此任务暂无历史日志。后续运行记录将在这里显示。' : '任务启动后，运行记录将在这里显示。'}</MetaText>}
            </div>
          </section>
          {!!job?.node_progress?.length && <details><summary className="cursor-pointer text-meta text-ink-soft">节点当前状态</summary><div className="mt-2 space-y-1">{job.node_progress.map((n, i) => <div key={i} className="flex justify-between gap-2 text-meta"><span className="min-w-0 break-words">{n.label}</span><span className="shrink-0">{{SUCCEEDED:'完成',RUNNING:'运行中',PENDING:'等待',READY:'就绪',FAILED:'失败',CANCELLED:'停止',WAITING:'等待返回'}[n.status] || n.status}</span></div>)}</div></details>}
          <a className="block text-meta text-accent-700" href={`http://localhost:3010/${job ? `?workflow=${encodeURIComponent(job.workflow_id)}` : ''}`} target="_blank" rel="noreferrer">打开画布编辑工作流</a>
        </div>}
        <div className="mt-3 flex gap-2">{active(job) && <Button size="sm" onClick={() => void api<Job>(`/workflow-writing/jobs/${job!.id}/cancel`, {method: 'POST'}).then(j => useWindow.setState({job: j})).catch(e => useWindow.setState({error: String(e)}))}>停止</Button>}{job?.has_output && <Button size="sm" onClick={() => void api<{text: string}>(`/workflow-writing/jobs/${job.id}/output`).then(r => setOutput(r.text)).catch(e => useWindow.setState({error: String(e)}))}>查看模型输出</Button>}</div>
      </div>
    </aside>
    <Modal open={output !== null} onClose={() => setOutput(null)} title="本次工作流原始输出" width="max-w-4xl"><TextContent label="工作流输出" text={output || ''} className="paper-text"/></Modal>
  </>
}
