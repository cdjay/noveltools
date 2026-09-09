import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { AArrowDown, AArrowUp, BookOpenText, Eye, FileDiff, FilePlus2, History, ListFilter, Maximize2, Minimize2, PanelLeft, PanelRight, PenLine, RotateCcw, Save, Trash2 } from 'lucide-react'
import { diff_match_patch } from 'diff-match-patch'
import { api } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import { useUiPrefs } from '../stores/useUiPrefs'
import type { Chapter, Version, SummaryResult } from '../types'
import { Badge, Button, ConfirmModal, Empty, Field, InlineError, Input, MetaText, Modal, Panel, SectionTitle, Select, StatusBadge, toast } from '../lib/ui'
import { ImportChapters } from '../lib/ImportChapters'
import { ReorderChapters } from '../lib/ReorderChapters'
import { SummaryTaskPanel } from '../lib/SummaryTaskPanel'
import { WritingTaskControl } from '../lib/WritingTaskControl'
import { CopyButton } from '../lib/CopyButton'
import { FormatSwitch, TextContent, TextEditor } from '../lib/TextContent'
import { useUnsavedChanges } from '../lib/useUnsavedChanges'
import { ChapterPlanPanel } from '../lib/ChapterPlanPanel'

function countWords(text: string) { return text.replace(/\s/g, '').length }

function DiffView({ left, right }: { left: string; right: string }) {
  const diffs = useMemo(() => { const dmp = new diff_match_patch(); return dmp.diff_main(left, right) }, [left, right])
  return <div className="paper-text rounded-md bg-paper-sunken p-4 text-[13px] leading-7">
    {diffs.map(([kind, text], index) => <span key={index} className={kind === 1 ? 'bg-ok-soft text-ok-strong' : kind === -1 ? 'bg-danger-soft text-danger-strong line-through' : ''}>{text}</span>)}
  </div>
}

const versionSourceLabel: Record<string, string> = { manual: '手动保存', import: '导入', imported: '导入', revised: '版本恢复', auto: '自动' }

export function ReaderPage() {
  const [searchParams] = useSearchParams()
  const { currentBook, chapters, setChapters, currentChapter, setCurrentChapter } = useAppStore()
  const prefs = useUiPrefs()
  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [versions, setVersions] = useState<Version[]>([])
  const [saving, setSaving] = useState(false)
  const [creating, setCreating] = useState(false)
  const [summarizing, setSummarizing] = useState(false)
  const [batchSummarizing, setBatchSummarizing] = useState(false)
  const [writingBusy, setWritingBusy] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [focusMode, setFocusMode] = useState(false)
  const [diffPair, setDiffPair] = useState<{ left: number | null; right: number | null }>({ left: null, right: null })
  const [showDiff, setShowDiff] = useState(false)
  const [historyPreview, setHistoryPreview] = useState<{ number: number; content: string } | null>(null)
  const [restoring, setRestoring] = useState<Version | null>(null)
  const [restoreBusy, setRestoreBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [pendingSwitch, setPendingSwitch] = useState<Chapter | null>(null)
  const readScrollRef = useRef<HTMLDivElement>(null)
  const positionTimer = useRef<number | undefined>(undefined)
  const pendingPosition = useRef<{ id: number; top: number } | null>(null)
  const flushPosition = () => {
    window.clearTimeout(positionTimer.current)
    const pending = pendingPosition.current
    pendingPosition.current = null
    if (pending) void api(`/chapters/${pending.id}`, { method: 'PUT', keepalive: true, body: JSON.stringify({ reading_position: pending.top }) }).catch(() => undefined)
  }
  const requestId = useRef(0)
  const initialized = useRef(false)
  const [loadingChapter, setLoadingChapter] = useState(false)
  const [draftRestored, setDraftRestored] = useState(false)
  const baseVersion = useRef<number | null | undefined>(null)
  const draftKey = (id: number) => `noveltools:draft:${currentBook?.id}:${id}`
  const clearDraft = (id: number) => { try { localStorage.removeItem(draftKey(id)) } catch { /* Storage may be unavailable. */ } }
  const rememberDraft = (nextContent: string, nextTitle: string) => {
    if (!currentChapter) return
    try { localStorage.setItem(draftKey(currentChapter.id), JSON.stringify({ content: nextContent, title: nextTitle, base: baseVersion.current })) }
    catch { setError('浏览器草稿空间不足，请及时保存正文。') }
  }
  useEffect(() => () => { requestId.current++; initialized.current = false; flushPosition() }, [])

  const dirty = !!currentChapter && (content !== currentChapter.current_content || title !== currentChapter.title)
  const editing = prefs.readerMode === 'edit'
  const currentVersion = versions.find(v => v.id === currentChapter?.current_version_id) || versions[0]

  /* ---------- 章节切换（含未保存保护） ---------- */

  const applyChapter = async (chapter: Chapter) => {
    const token = ++requestId.current
    flushPosition()
    setLoadingChapter(true); setError(''); setVersions([])
    try {
      const fresh = await api<Chapter>(`/chapters/${chapter.id}`)
      if (token !== requestId.current) return
      setCurrentChapter(fresh)
      const state = useAppStore.getState()
      state.setChapters(state.chapters.map(item => item.id === fresh.id ? fresh : item))
      setContent(fresh.current_content); setTitle(fresh.title); baseVersion.current = fresh.current_version_id
      setDraftRestored(false)
      try {
        const stored = localStorage.getItem(draftKey(chapter.id))
        if (stored) {
          const draft = JSON.parse(stored)
          if (typeof draft.content === 'string' && typeof draft.title === 'string' &&
              (draft.content !== fresh.current_content || draft.title !== fresh.title)) {
            setContent(draft.content); setTitle(draft.title); baseVersion.current = draft.base
            setDraftRestored(true); prefs.setReaderMode('edit')
          }
        }
      } catch { /* A damaged browser draft cannot replace the saved text. */ }
      if (state.currentBook) {
        state.setCurrentBook({ ...state.currentBook, current_chapter_id: fresh.id })
        void api(`/books/${fresh.book_id}`, { method: 'PUT', body: JSON.stringify({ current_chapter_id: fresh.id }) }).catch(() => setError('阅读章节记录失败，请检查服务连接。'))
      }
      setDiffPair({ left: null, right: null }); setShowDiff(false)
      const history = await api<Version[]>(`/chapters/${chapter.id}/versions?include_content=false`)
      if (token === requestId.current) setVersions(history)
    } catch (exc) { if (token === requestId.current) setError(String(exc)) }
    finally { if (token === requestId.current) setLoadingChapter(false) }
  }
  const requestChapter = (chapter: Chapter) => {
    if (saving || restoreBusy || chapter.id === currentChapter?.id) return
    if (dirty) setPendingSwitch(chapter)
    else void applyChapter(chapter)
  }
  useEffect(() => { if (!initialized.current && chapters.length) { initialized.current = true; void applyChapter(chapters.find(item => item.id === Number(searchParams.get('chapter'))) || chapters.find(item => item.id === currentBook?.current_chapter_id) || chapters[0]) } }, [currentBook?.id, chapters.length])
  useUnsavedChanges(dirty)


  /* 阅读模式下恢复并持续记录阅读位置 */
  useEffect(() => {
    if (!editing && readScrollRef.current && currentChapter) readScrollRef.current.scrollTop = currentChapter.reading_position || 0
  }, [currentChapter?.id, editing])
  const recordPosition = () => {
    if (editing || !currentChapter) return
    const id = currentChapter.id
    const top = Math.round(readScrollRef.current?.scrollTop ?? 0)
    const state = useAppStore.getState()
    state.setChapters(state.chapters.map(item => item.id === id ? { ...item, reading_position: top } : item))
    state.setCurrentChapter({ ...currentChapter, reading_position: top })
    window.clearTimeout(positionTimer.current)
    pendingPosition.current = { id, top }
    positionTimer.current = window.setTimeout(flushPosition, 200)
  }

  /* ---------- 业务操作（语义与原实现一致） ---------- */

  const save = async () => {
    if (!currentChapter || loadingChapter || saving) return false
    setSaving(true); setError('')
    try {
      const version = await api<Version>(`/chapters/${currentChapter.id}/versions`, { method: 'POST', body: JSON.stringify({ content, title, base_version_id: baseVersion.current, note: '阅读器手动保存' }) })
      const updated = { ...currentChapter, title, current_content: content, current_version_id: version.id, word_count: version.word_count, summary_is_stale: !!currentChapter.summary }
      if (useAppStore.getState().currentChapter?.id !== updated.id) return true
      baseVersion.current = version.id
      clearDraft(updated.id); setDraftRestored(false)
      setCurrentChapter(updated); setChapters(useAppStore.getState().chapters.map(item => item.id === updated.id ? updated : item))
      setVersions([version, ...versions])
      toast(`已保存为 v${version.version_number}，旧版本可恢复`)
      return true
    } catch (exc) { setError(String(exc)); toast('保存失败', 'danger'); return false } finally { setSaving(false) }
  }
  const createChapter = async () => {
    if (!currentBook || creating || saving || loadingChapter || (dirty && !window.confirm('放弃当前未保存的修改并新建章节？'))) return
    setCreating(true); setError('')
    try {
      const chapter = await api<Chapter>(`/books/${currentBook.id}/chapters`, { method: 'POST', body: JSON.stringify({ title: `第${chapters.length + 1}章`, content: '' }) })
      if (useAppStore.getState().currentBook?.id !== currentBook.id) return
      if (currentChapter && dirty) clearDraft(currentChapter.id)
      setChapters([...useAppStore.getState().chapters, chapter]); void applyChapter(chapter)
      toast(`已创建「${chapter.title}」`)
    } catch (exc) { setError(String(exc)) }
    finally { setCreating(false) }
  }
  const imported = (added: Chapter[]) => {
    setChapters([...useAppStore.getState().chapters, ...added])
    if (added[0]) void applyChapter(added[0])
  }
  const removeChapter = async () => {
    if (!currentChapter) return
    setDeleteBusy(true)
    try {
      await api(`/chapters/${currentChapter.id}?confirm=true`, { method: 'DELETE' })
      const next = chapters.filter(item => item.id !== currentChapter!.id)
      setChapters(next); setDeleting(false); setCurrentChapter(null)
      if (next[0]) void applyChapter(next[0])
      toast('章节已删除')
    } catch (exc) { setError(String(exc)) } finally { setDeleteBusy(false) }
  }
  const restoreVersion = async () => {
    if (!currentChapter || !restoring || dirty || saving || loadingChapter) return
    setRestoreBusy(true)
    try {
      const version = await api<Version>(`/chapters/${currentChapter.id}/versions/${restoring.id}/restore?base_version_id=${currentChapter.current_version_id}`, { method: 'POST' })
      const updated = await api<Chapter>(`/chapters/${currentChapter.id}`)
      setCurrentChapter(updated); setChapters(chapters.map(item => item.id === updated.id ? updated : item))
      baseVersion.current = version.id; clearDraft(updated.id); setDraftRestored(false)
      setContent(version.content); setTitle(updated.title); setVersions([version, ...versions]); setRestoring(null)
      toast(`已从 v${restoring.version_number} 恢复，生成新版本 v${version.version_number}`)
    } catch (exc) { setError(String(exc)) } finally { setRestoreBusy(false) }
  }
  const mergeSummaries = (results: SummaryResult[]) => {
    const state = useAppStore.getState()
    if (state.currentBook?.id !== currentBook?.id) return
    const merge = (chapter: Chapter) => {
      const fresh = results.find(item => item.chapter_id === chapter.id)?.chapter
      if (!fresh) return chapter
      return { ...chapter, summary: fresh.summary, summary_content_sha256: fresh.summary_content_sha256,
        summary_source_version_id: fresh.summary_source_version_id, summary_model: fresh.summary_model,
        summary_updated_at: fresh.summary_updated_at,
        summary_is_stale: fresh.summary_is_stale || fresh.current_version_id !== chapter.current_version_id }
    }
    state.setChapters(state.chapters.map(merge))
    if (state.currentChapter) state.setCurrentChapter(merge(state.currentChapter))
  }
  const generateSummary = async () => {
    if (!currentChapter || dirty || saving || summarizing || batchSummarizing) return
    setSummarizing(true); setError('')
    if (!prefs.readerShowContext) prefs.toggleReaderContext()
    setFocusMode(false)
    try {
      const result = await api<SummaryResult>(`/chapters/${currentChapter.id}/summary`, { method: 'POST' })
      mergeSummaries([result])
      if (result.status === 'failed') setError(result.message)
      toast(result.message, result.status === 'failed' ? 'danger' : 'ok')
    } catch (exc) { setError(String(exc)); toast('梗概生成失败，已有内容保留', 'danger') }
    finally { setSummarizing(false) }
  }
  const refreshSummaries = async () => {
    if (!currentBook) return
    try {
      const fresh = await api<Chapter[]>(`/books/${currentBook.id}/chapters?include_content=false`)
      mergeSummaries(fresh.map(chapter => ({ chapter_id: chapter.id, status: 'completed', updated: true, skipped: false, message: '', chapter })))
    } catch { /* The task panel reports connection errors and retries. */ }
  }
  const [diffVersions, setDiffVersions] = useState<{ left: { content: string }; right: { content: string } } | null>(null)
  useEffect(() => {
    let active = true
    setDiffVersions(null)
    if (showDiff && currentChapter && diffPair.left && diffPair.right)
      void api<{ left: { content: string }; right: { content: string } }>(`/chapters/${currentChapter.id}/diff?left=${diffPair.left}&right=${diffPair.right}`)
        .then(result => { if (active) setDiffVersions(result) }).catch(exc => { if (active) setError(String(exc)) })
    return () => { active = false }
  }, [showDiff, currentChapter?.id, diffPair.left, diffPair.right])
  const visible = chapters.filter(item => `${item.title}${item.chapter_number}`.includes(search))
  const showChapters = prefs.readerShowChapters && !focusMode
  const showContext = prefs.readerShowContext && !focusMode

  if (chapters.length === 0) return <div className="flex h-full items-center justify-center p-8">
    {error && <InlineError message={error} />}
    <Empty icon={<BookOpenText size={24} />} title="这本书还没有章节" text="新建第一章开始写作，或导入已有的 TXT / Markdown 正文。" action={
      <div className="flex gap-2">
        {currentBook && <Link className="rounded-md border border-line px-3 py-2 text-body text-accent-700" to={`/books/${currentBook.id}/chapter-plans`}>先规划章纲</Link>}
        {currentBook && <ImportChapters bookId={currentBook.id} disabled={false} onImported={imported} />}
        <Button variant="primary" disabled={creating || saving || loadingChapter} onClick={() => void createChapter()}><FilePlus2 size={15} />新建第一章</Button>
      </div>} />
  </div>

  return <div className="relative flex h-full min-h-0">

    {/* 左栏：章节列表 */}
    <aside className={`${showChapters ? 'flex' : 'hidden'} w-60 shrink-0 flex-col border-r border-line bg-paper-raised`} aria-label="章节列表">
      <div className="border-b border-line-soft p-3">
        <div className="flex items-center justify-between">
          <SectionTitle>章节 · {chapters.length}</SectionTitle>
          <Button size="sm" variant="subtle" disabled={creating || saving || loadingChapter} onClick={() => void createChapter()}><FilePlus2 size={13} />新建</Button>
        </div>
        <div className="relative mt-2.5">
          <ListFilter size={13} className="pointer-events-none absolute z-10 left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
          <Input className="py-1.5 pl-7 text-meta" placeholder="搜索章节" value={search} onChange={e => setSearch(e.target.value)} aria-label="搜索章节" />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2 scrollbar-thin">
        {visible.length === 0 ? <MetaText className="py-8 text-center">{search ? '没有匹配的章节' : '暂无章节'}</MetaText> : visible.map(chapter => {
          const active = currentChapter?.id === chapter.id
          return <button key={chapter.id} onClick={() => requestChapter(chapter)} aria-current={active ? 'true' : undefined}
            className={`mb-0.5 w-full rounded-md border-l-2 px-3 py-2.5 text-left transition-colors duration-fast ${active ? 'border-accent-600 bg-accent-50' : 'border-transparent hover:bg-paper-sunken'}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-meta tabular-nums text-ink-faint">第 {chapter.chapter_number} 章</span>
              <StatusBadge status={chapter.status} label={chapter.status === 'draft' ? '草稿' : chapter.status === 'approved' ? '已审定' : chapter.status} />
            </div>
            <div className={`mt-0.5 truncate text-body font-medium ${active ? 'text-accent-800' : 'text-ink'}`}>{chapter.title}</div>
            <div className="mt-0.5 text-meta tabular-nums text-ink-faint">{chapter.word_count.toLocaleString()} 字</div>
          </button>
        })}
      </div>
      <div className="border-t border-line-soft p-2.5">
        {currentBook && <SummaryTaskPanel bookId={currentBook.id} disabled={dirty || saving || summarizing || writingBusy} onBusy={setBatchSummarizing} onProgress={() => void refreshSummaries()} />}
        {currentBook && <div className="mt-1 flex flex-col"><ImportChapters bookId={currentBook.id} disabled={dirty || saving || loadingChapter} onImported={imported} /><ReorderChapters bookId={currentBook.id} chapters={chapters} disabled={dirty || saving || loadingChapter} onSaved={next => { setChapters(next); if (currentChapter) { const fresh = next.find(item => item.id === currentChapter.id); if (fresh) setCurrentChapter({ ...currentChapter, chapter_number: fresh.chapter_number }) } }} /></div>}
      </div>
    </aside>

    {/* 中栏：正文 */}
    <div className="flex min-w-0 flex-1 flex-col bg-paper">
      {/* 工具栏 */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-line bg-paper-raised px-4 py-2">
        <Button size="sm" variant="ghost" title="章节列表" aria-label="切换章节列表" onClick={() => { if (focusMode) setFocusMode(false); else prefs.toggleReaderChapters() }}><PanelLeft size={15} /></Button>
        <div className="flex items-center rounded-md border border-line" role="group" aria-label="阅读与编辑模式">
          <button onClick={() => prefs.setReaderMode('read')} className={`flex items-center gap-1 rounded-l-md px-2.5 py-1.5 text-meta transition-colors ${!editing ? 'bg-accent-600 text-white' : 'text-ink-soft hover:bg-paper-sunken'}`}><Eye size={13} />阅读</button>
          <button onClick={() => prefs.setReaderMode('edit')} className={`flex items-center gap-1 rounded-r-md px-2.5 py-1.5 text-meta transition-colors ${editing ? 'bg-accent-600 text-white' : 'text-ink-soft hover:bg-paper-sunken'}`}><PenLine size={13} />编辑</button>
        </div>
        <div className="flex items-center gap-0.5" role="group" aria-label="字号">
          <Button size="sm" variant="ghost" title="减小字号" aria-label="减小字号" onClick={() => prefs.setReaderFontSize(prefs.readerFontSize - 1)}><AArrowDown size={15} /></Button>
          <Button size="sm" variant="ghost" title="增大字号" aria-label="增大字号" onClick={() => prefs.setReaderFontSize(prefs.readerFontSize + 1)}><AArrowUp size={16} /></Button>
        </div>
        <Button size="sm" variant="ghost" title={focusMode ? '退出专注模式' : '专注模式'} onClick={() => setFocusMode(!focusMode)}>{focusMode ? <Minimize2 size={15} /> : <Maximize2 size={15} />}{focusMode ? '退出专注' : '专注'}</Button>
        <div className="mx-1 h-5 w-px bg-line" />
        {dirty && <Badge tone="warn">有未保存修改</Badge>}
        <div className="ml-auto flex max-w-full flex-wrap items-center gap-1.5">
          {currentBook && <WritingTaskControl key={currentBook.id} bookId={currentBook.id} disabled={dirty || saving || loadingChapter || summarizing || batchSummarizing}
            onBusy={setWritingBusy} onOpen={id => {
              const chapter = useAppStore.getState().chapters.find(c => c.id === id)
              if (chapter) requestChapter(chapter)
            }} />}
          <Button size="sm" variant="secondary" disabled={!currentChapter || dirty || saving || summarizing || batchSummarizing || writingBusy} title={dirty ? '请先保存正文修改，再生成梗概' : '使用已保存的本章正文生成梗概'} onClick={() => void generateSummary()}>{summarizing ? '梗概生成中…' : '生成本章梗概'}</Button>
          <Button size="sm" variant="secondary" disabled={saving || loadingChapter || !currentChapter} onClick={() => void save()}><Save size={14} />{saving ? '保存中…' : '保存新版本'}</Button>
          <Button size="sm" variant="ghost" title="梗概与历史" aria-label="切换梗概与历史" onClick={() => { if (focusMode) setFocusMode(false); else prefs.toggleReaderContext() }}><PanelRight size={15} /></Button>
        </div>
      </div>
      {error && <div className="shrink-0 px-4 pt-3"><InlineError message={error} /></div>}

      {draftRestored && <div className="px-4 py-2 text-meta text-warn-strong">已恢复浏览器中的未保存草稿；保存时仍会检查版本冲突。</div>}
      {(dirty || error) && currentChapter && <Button size="sm" variant="ghost" onClick={() => { if (!dirty || window.confirm('放弃当前草稿并重新加载已保存正文？')) { clearDraft(currentChapter.id); void applyChapter(currentChapter) } }}>重新加载已保存正文</Button>}
      {/* 正文区 */}
      {currentChapter ? (
        editing ? <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-thin">
          <div className="mx-auto w-full max-w-measure flex-1 px-6 py-6">
            <Input className="border-transparent bg-transparent px-0 font-serif text-lg font-semibold focus:border-line" disabled={saving || restoreBusy || loadingChapter} value={title} onChange={e => { setTitle(e.target.value); rememberDraft(content, e.target.value) }} aria-label="章节标题" placeholder="章节标题" />
            <TextEditor className="paper-text mt-3 min-h-[60vh] resize-y border-0 bg-transparent px-0 shadow-none focus:border-transparent" style={{ fontSize: prefs.readerFontSize }} disabled={saving || restoreBusy || loadingChapter} value={content} onChange={e => { setContent(e.target.value); rememberDraft(e.target.value, title) }} placeholder="从这里开始写正文，或导入已有章节…" aria-label="章节正文" />
          </div>
        </div>
        : <div ref={readScrollRef} onScroll={recordPosition} className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
          <article className="mx-auto max-w-measure px-6 py-8">
            <MetaText>第 {currentChapter.chapter_number} 章</MetaText>
            <h1 className="mt-1 font-serif text-2xl font-bold text-ink">{currentChapter.title}</h1>
            <TextContent label="正文内容" className="mt-6 paper-text" style={{ fontSize: prefs.readerFontSize }} text={currentChapter.current_content || '本章还没有正文。切换到「编辑」开始写作。'} />
          </article>
        </div>
      ) : <div className="flex flex-1 items-center justify-center"><MetaText>从左侧选择一章</MetaText></div>}

      {/* 状态栏 */}
      {currentChapter && <div className="flex shrink-0 items-center justify-between border-t border-line bg-paper-raised px-4 py-1.5 text-meta tabular-nums text-ink-faint">
        <span>{editing ? `编辑中 · ${countWords(content).toLocaleString()} 字` : `${currentChapter.word_count.toLocaleString()} 字`}{currentVersion ? ` · 当前 v${currentVersion.version_number}` : ''}</span>
        <span>{dirty ? '修改尚未保存，切换章节前会提醒你' : '所有修改已保存为版本'}</span>
      </div>}
    </div>

    {/* 右栏：本章梗概与版本历史 */}
    {showContext && currentChapter && <aside className="absolute inset-y-0 right-0 z-20 flex w-72 max-w-full shrink-0 flex-col overflow-y-auto border-l border-line bg-paper-raised shadow-pop scrollbar-thin xl:static xl:shadow-none" aria-label="章节历史与管理">
      <div className="space-y-4 p-3.5"><Button size="sm" variant="ghost" onClick={prefs.toggleReaderContext}>关闭侧栏</Button>
        {currentBook && <ChapterPlanPanel bookId={currentBook.id} chapterId={currentChapter.id} versionId={currentChapter.current_version_id ?? null} />}
        <Panel title="本章梗概">
          {currentChapter.summary_is_stale && <Badge tone="warn">正文已变化，建议重新生成</Badge>}
          <TextContent label="本章梗概内容" className="mt-2 text-body leading-7 text-ink-soft" text={currentChapter.summary || '尚未生成梗概。点击上方“生成本章梗概”，使用 LLM 整理本章内容。'} />
          {currentChapter.summary_updated_at && <MetaText className="mt-2">{currentChapter.summary_model} · {new Date(currentChapter.summary_updated_at).toLocaleString()}</MetaText>}
          <MetaText className="mt-2">{dirty ? '正文有未保存修改，请先保存再生成。' : '按已保存正文生成；批量更新跳过正文未变化的章节。'}</MetaText>
        </Panel>
        <Panel title={<span className="inline-flex items-center gap-1.5"><History size={14} />版本历史</span>} action={<Button size="sm" variant="ghost" onClick={() => setShowDiff(true)} disabled={versions.length < 2}><FileDiff size={13} />对比</Button>}>
          <div className="space-y-1.5">
            {versions.map(version => {
              const isCurrent = version.id === currentChapter.current_version_id
              return <div key={version.id} className={`rounded-md border px-3 py-2 ${isCurrent ? 'border-accent-200 bg-accent-50/60' : 'border-line-soft'}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-body font-medium text-ink">v{version.version_number}{isCurrent && <Badge tone="accent">当前</Badge>}</span>
                  <span className="text-meta text-ink-faint">{versionSourceLabel[version.source] || version.source}</span>
                </div>
                <MetaText className="mt-0.5">{version.word_count.toLocaleString()} 字 · {new Date(version.created_at).toLocaleString()}</MetaText>
                {version.note && <TextContent label="版本备注" className="mt-0.5 text-meta text-ink-faint" text={version.note} />}
                <Button size="sm" variant="ghost" onClick={async () => { try { const result = await api<{ left: { content: string } }>(`/chapters/${version.chapter_id}/diff?left=${version.id}&right=${version.id}`); setHistoryPreview({ number: version.version_number, content: result.left.content }) } catch (exc) { setError(String(exc)) } }}>查看 v{version.version_number}</Button>
                {!isCurrent && <Button size="sm" variant="ghost" className="mt-1 px-1.5 text-accent-700" disabled={dirty || saving || loadingChapter} onClick={() => setRestoring(version)}><RotateCcw size={12} />恢复到此版本</Button>}
              </div>
            })}
          </div>
        </Panel>

        <Panel title="章节管理">
          <MetaText>状态：{currentChapter.status === 'draft' ? '草稿' : currentChapter.status} · 最近更新 {new Date(currentChapter.updated_at).toLocaleString()}</MetaText>
          <Button size="sm" variant="danger" className="mt-3" onClick={() => setDeleting(true)}><Trash2 size={13} />删除本章</Button>
        </Panel>
      </div>
    </aside>}

    {/* 未保存切换确认 */}
    <Modal open={!!pendingSwitch} onClose={() => setPendingSwitch(null)} title="当前章节有未保存的修改" width="max-w-md"
      footer={<>
        <Button variant="ghost" onClick={() => setPendingSwitch(null)}>取消</Button>
        <Button variant="danger" onClick={() => { const target = pendingSwitch; if (currentChapter) clearDraft(currentChapter.id); setPendingSwitch(null); if (target) void applyChapter(target) }}>放弃修改并切换</Button>
        <Button variant="primary" disabled={saving} onClick={async () => { if (!await save()) return; const target = pendingSwitch; setPendingSwitch(null); if (target) void applyChapter(target) }}><Save size={14} />保存并切换</Button>
      </>}>
      <p className="text-body leading-7 text-ink-soft">「{currentChapter?.title}」的正文或标题已修改但尚未保存为版本。保存会生成一个新版本；放弃则丢失本次未保存的修改。</p>
    </Modal>

    {/* 版本恢复确认 */}
    <ConfirmModal open={!!restoring} onClose={() => setRestoring(null)} onConfirm={() => void restoreVersion()} busy={restoreBusy}
      title={`恢复到 v${restoring?.version_number}？`} confirmText="恢复为新版本"
      description={<>不会覆盖任何历史版本：系统会把 v{restoring?.version_number} 的内容复制为一个<strong>新版本</strong>并设为当前版本。</>} />

    {/* 删除章节确认 */}
    <ConfirmModal open={deleting} onClose={() => setDeleting(false)} onConfirm={() => void removeChapter()} danger busy={deleteBusy}
      title={`删除「${currentChapter?.title}」？`} confirmText="永久删除"
      description={<>将删除本章及其全部 {versions.length} 个历史版本，<strong className="text-danger-strong">不可撤销</strong>。</>} />

    <Modal open={!!historyPreview} onClose={() => setHistoryPreview(null)} title={`正文历史 · v${historyPreview?.number}`} width="max-w-4xl" footer={<Button variant="ghost" onClick={() => setHistoryPreview(null)}>关闭</Button>}>
      <div className="mb-4"><FormatSwitch /></div>
      <TextContent text={historyPreview?.content || ''} label="历史正文全文" className="paper-text" />
    </Modal>
    {/* 版本对比 */}
    <Modal open={showDiff} onClose={() => setShowDiff(false)} title="版本对比" description="选择两个版本查看字符级差异：红色删除线是被移除的内容，绿色是新增内容。" width="max-w-4xl"
      footer={<Button variant="ghost" onClick={() => setShowDiff(false)}>关闭</Button>}>
      <div className="mb-4"><FormatSwitch /></div>
      <div className="mb-4 grid grid-cols-2 gap-3">
        {(['left', 'right'] as const).map(side => <Field key={side} label={side === 'left' ? '旧版本（左）' : '新版本（右）'}>
          <Select value={diffPair[side] || ''} onChange={e => setDiffPair({ ...diffPair, [side]: Number(e.target.value) || null })}>
            <option value="">选择版本</option>
            {versions.map(version => <option key={version.id} value={version.id}>v{version.version_number} · {new Date(version.created_at).toLocaleDateString()}</option>)}
          </Select>
        </Field>)}
      </div>
      {diffVersions?.left && diffVersions?.right
        ? prefs.textFormat === 'markdown' ? <div className="grid gap-4 md:grid-cols-2"><section><MetaText>旧版本</MetaText><TextContent text={diffVersions.left.content} label="旧版本正文" className="paper-text" /></section><section><MetaText>新版本</MetaText><TextContent text={diffVersions.right.content} label="新版本正文" className="paper-text" /></section></div> : <><DiffView left={diffVersions.left.content} right={diffVersions.right.content} /><div className="mt-3 flex items-center gap-2 text-meta"><span>旧版本</span><CopyButton text={diffVersions.left.content} label="旧版本正文" /><span>新版本</span><CopyButton text={diffVersions.right.content} label="新版本正文" /></div></>
        : <Empty compact title="请选择两个版本" text="通常左边选较早的版本，右边选较晚的版本。" />}
    </Modal>

  </div>
}
