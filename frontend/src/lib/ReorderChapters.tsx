import { useState } from 'react'
import { api } from '../api/client'
import type { Chapter } from '../types'
import { Button, InlineError, Modal, toast } from './ui'

export function ReorderChapters({ bookId, chapters, disabled, onSaved }: { bookId: number; chapters: Chapter[]; disabled: boolean; onSaved: (chapters: Chapter[]) => void }) {
  const [order, setOrder] = useState<Chapter[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const move = (index: number, offset: number) => {
    if (!order) return
    const next = [...order]; [next[index], next[index + offset]] = [next[index + offset], next[index]]; setOrder(next)
  }
  const save = async () => {
    setBusy(true); setError('')
    try {
      const result = await api<Chapter[]>(`/books/${bookId}/chapters/reorder`, { method: 'POST', body: JSON.stringify({ chapter_ids: order?.map(chapter => chapter.id) }) })
      onSaved(result); setOrder(null); toast('章节顺序已保存')
    } catch (exc) { setError(String(exc)) }
    finally { setBusy(false) }
  }
  return <>
    <Button size="sm" variant="ghost" disabled={disabled || chapters.length < 2} onClick={() => { setOrder(chapters); setError('') }}>调整章节顺序</Button>
    <Modal open={!!order} onClose={() => { if (!busy) setOrder(null) }} title="调整章节顺序" description="保存后重新编号，正文与历史版本跟随原章节保留。"
      footer={<><Button variant="ghost" disabled={busy} onClick={() => setOrder(null)}>取消</Button><Button variant="primary" disabled={busy} onClick={() => void save()}>保存顺序</Button></>}>
      {error && <InlineError message={error} />}
      <div className="max-h-96 overflow-y-auto">{order?.map((chapter, index) => <div key={chapter.id} className="flex items-center gap-2 border-b border-line-soft py-2"><span className="min-w-0 flex-1 truncate">{index + 1}. {chapter.title}</span><Button size="sm" disabled={busy || index === 0} aria-label={`上移 ${chapter.title}`} onClick={() => move(index, -1)}>上移</Button><Button size="sm" disabled={busy || index === order.length - 1} aria-label={`下移 ${chapter.title}`} onClick={() => move(index, 1)}>下移</Button></div>)}</div>
    </Modal>
  </>
}
