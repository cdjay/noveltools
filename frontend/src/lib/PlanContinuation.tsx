import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { Button, Field, InlineError, Input, MetaText, Modal, Select } from './ui'

type Job = {id:string;book_id:number;status:string;message:string;target_count:number;completed_count:number;workflow_id:string;run_id:string|null;round:number;can_resume:boolean;has_output:boolean;items:{plan_id:string;position:number;title:string;run_id:string}[]}
const active = (j:Job|null) => !!j && ['queued','running','submitting','stopping'].includes(j.status)

type PlanContinuationProps = {
  bookId:number
  disabled:boolean
  onUpdated:()=>Promise<void>
  onBusy:(busy:boolean)=>void
  label?: string
  initialMode?: 'append' | 'regenerate'
  initialStartPosition?: number
  initialCount?: number
  modalTitle?: string
}

export function PlanContinuation({
  bookId, disabled, onUpdated, onBusy, label = '章纲续写',
  initialMode = 'append', initialStartPosition, initialCount = 1,
  modalTitle,
}: PlanContinuationProps) {
  const [open,setOpen] = useState(false)
  const [count,setCount] = useState(String(initialCount))
  const [mode,setMode] = useState<'append' | 'regenerate'>(initialMode)
  const [startPosition,setStartPosition] = useState(String(initialStartPosition ?? 1))
  const [workflow,setWorkflow] = useState('')
  const [choices,setChoices] = useState<{id:string;name:string}[]>([])
  const [volume,setVolume] = useState('')
  const [requirements,setRequirements] = useState('')
  const [job,setJob] = useState<Job|null>(null)
  const [error,setError] = useState('')
  const [sending,setSending] = useState(false)
  const [output,setOutput] = useState('')
  const [ready,setReady] = useState(false)
  const latest = useRef(onUpdated);latest.current=onUpdated
  const updated = useRef('')
  useEffect(()=>{let live=true;setReady(false);setJob(null);setError('');updated.current=''
    setMode(initialMode)
    setStartPosition(String(initialStartPosition ?? 1))
    setCount(String(initialCount))
    Promise.all([api<Job|null>(`/books/${bookId}/plan-writing/latest`),api<{workflow_id:string}>(`/books/${bookId}/plan-writing/settings`),api<{id:string;name:string}[]>('/canvas-writing/workflows').catch(()=>[])])
      .then(([j,s,list])=>{if(live){setJob(j);setWorkflow(s.workflow_id);setChoices(list);setReady(true)}}).catch(e=>{if(live)setError(String(e))})
    return()=>{live=false}
  },[bookId, initialCount, initialMode, initialStartPosition])
  useEffect(()=>{onBusy(active(job)||sending);return()=>onBusy(false)},[job?.status,sending,onBusy])
  useEffect(()=>{if(!job)return;let live=true;let timer:ReturnType<typeof setTimeout>
    const poll=async()=>{try{const j=await api<Job>(`/plan-writing/jobs/${job.id}`);if(!live)return;setJob(j)
      const key=`${j.id}:${j.completed_count}`
      if(updated.current!==key){await latest.current();if(!live)return;updated.current=key}
      setError('');if(active(j))timer=setTimeout(()=>void poll(),1800)
    }catch(e){if(live){setError(String(e));timer=setTimeout(()=>void poll(),4000)}}}
    void poll();return()=>{live=false;clearTimeout(timer)}
  },[job?.id,job?.status])
  const perform=async(fn:()=>Promise<Job>)=>{if(sending)return;setSending(true);setError('');try{setJob(await fn());setOutput('')}catch(e){setError(String(e))}finally{setSending(false)}}
  const start=()=>perform(()=>api<Job>(`/books/${bookId}/plan-writing`,{method:'POST',body:JSON.stringify({chapter_count:Number(count),workflow_id:workflow,volume,requirements,...(mode==='regenerate'?{start_position:Number(startPosition)}:{})})}))
  return <>
    <Button disabled={disabled||sending} onClick={()=>{setMode(initialMode);setStartPosition(String(initialStartPosition ?? 1));setCount(String(initialCount));setOpen(true)}}>{label}{active(job)?` · ${job!.completed_count}/${job!.target_count}`:''}</Button>
    {job&&<span className="text-meta text-ink-soft" role="status">{job.message}</span>}
    <Modal open={open} onClose={()=>setOpen(false)} title={modalTitle || (initialMode === 'regenerate' ? '重写本章章纲' : '章纲续写')} width="max-w-2xl" footer={<>
      <Button onClick={()=>setOpen(false)}>关闭</Button>
      {active(job)?<Button disabled={sending||job?.status==='stopping'} onClick={()=>void perform(()=>api<Job>(`/plan-writing/jobs/${job!.id}/cancel`,{method:'POST'}))}>停止续写</Button>:<>
        {job?.can_resume&&<Button disabled={sending||disabled} onClick={()=>void perform(()=>api<Job>(`/plan-writing/jobs/${job.id}/resume`,{method:'POST'}))}>继续剩余章纲</Button>}
        <Button variant="primary" disabled={!ready||sending||disabled||!Number.isInteger(Number(count))||Number(count)<1||Number(count)>100||(mode==='regenerate'&&(!Number.isInteger(Number(startPosition))||Number(startPosition)<1))} onClick={()=>void start()}>{mode==='regenerate'?'开始重生成':'开始续写'}</Button>
      </>}
    </>}>
      <div className="space-y-4">
        <MetaText>{initialMode === 'regenerate' ? '以当前章已有正文和前文事实为依据反推新版章纲；旧版本保留在章纲历史中，正文绑定不变。' : '每章使用画布生成、审核和修订，通过后保存草稿再继续。关闭窗口不停止任务。'}</MetaText>
        {!active(job)&&<>
        {initialMode === 'append' ? <Field label="生成方式"><Select aria-label="章纲生成方式" value={mode} disabled={sending} onChange={e=>setMode(e.target.value as 'append' | 'regenerate')}><option value="append">从末尾续写</option><option value="regenerate">从指定章重新生成</option></Select></Field> : <MetaText>生成方式：重写第 {initialStartPosition} 章章纲</MetaText>}
        {mode==='regenerate'&&<Field label="起始章纲" hint="已有位置新增版本，超出末尾继续追加，不删除后面的章纲。"><Input aria-label="起始章纲位置" type="number" min={1} step={1} value={startPosition} disabled={sending || initialStartPosition !== undefined} onChange={e=>setStartPosition(e.target.value)}/></Field>}
        <Field label="生成数量" hint="本次处理章纲数量，1—100。逐章生成和审核。"><Input aria-label="章纲生成数量" type="number" min={1} max={100} step={1} value={count} disabled={active(job)||sending} onChange={e=>setCount(e.target.value)}/></Field>
        <Field label="章纲工作流" hint="首次使用默认项会依据现有正文生成、审核模型创建独立章纲画布。可以在画布里调整模型和循环上限。"><Select aria-label="章纲工作流" disabled={active(job)||sending} value={workflow} onChange={e=>setWorkflow(e.target.value)}>
          <option value="">默认章纲循环工作流</option>{choices.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
          {workflow&&!choices.some(c=>c.id===workflow)&&<option value={workflow}>已保存章纲工作流</option>}
        </Select></Field>
        <Field label="所属卷（可选）"><Input value={volume} disabled={active(job)||sending} onChange={e=>setVolume(e.target.value)}/></Field>
        <Field label="本次要求（可选）"><textarea aria-label="章纲续写要求" className="min-h-24 w-full rounded-lg border border-line bg-paper p-3" value={requirements} disabled={active(job)||sending} onChange={e=>setRequirements(e.target.value)}/></Field>
        </>}
        {job&&<div className="space-y-2"><MetaText>{job.message}</MetaText><progress className="w-full" value={job.completed_count} max={job.target_count}/>
          {job.items.map(item=><div key={item.plan_id}>第 {item.position} 章 · {item.title} · 已保存</div>)}
          <a className="block text-accent-700" href={`http://localhost:3010/?workflow=${encodeURIComponent(job.workflow_id)}`} target="_blank" rel="noreferrer">打开章纲画布与逐轮记录</a>
          {job.has_output&&<Button onClick={()=>void api<{text:string}>(`/plan-writing/jobs/${job.id}/output`).then(r=>setOutput(r.text)).catch(e=>setError(String(e)))}>查看保留的输出</Button>}
        </div>}
        {output&&<pre className="max-h-80 overflow-auto whitespace-pre-wrap text-body">{output}</pre>}
        {error&&<InlineError message={error}/>}
      </div>
    </Modal>
  </>
}
