import { useEffect, useMemo, useRef, useState } from 'react'
import { AArrowDown, AArrowUp, BookOpenText, Clipboard, Copy, Eye, FileDiff, FilePlus2, History, ListFilter, Maximize2, Minimize2, PanelLeft, PanelRight, PenLine, RotateCcw, Save, Sparkles, Trash2, Upload, X } from 'lucide-react'
import { diff_match_patch } from 'diff-match-patch'
import { api, upload } from '../api/client'
import { useAppStore } from '../stores/useAppStore'
import { useUiPrefs } from '../stores/useUiPrefs'
import type { Analysis, Chapter, ChapterSummaryBatchResult, ChapterSummaryResult, Version } from '../types'
import { Badge, Button, ConfirmModal, Empty, Field, InlineError, InlineNotice, Input, MetaText, Modal, Panel, SectionTitle, Select, StatusBadge, statusText, Textarea, toast } from '../lib/ui'
import { AnalysisResultDetails } from '../components/AnalysisResultDetails'

function countWords(text: string) { return text.replace(/\s/g, '').length }

function DiffView({ left, right }: { left: string; right: string }) {
  const diffs = useMemo(() => { const dmp = new diff_match_patch(); return dmp.diff_main(left, right) }, [left, right])
  return <div className="paper-text rounded-md bg-paper-sunken p-4 text-[13px] leading-7">
    {diffs.map(([kind, text], index) => <span key={index} className={kind === 1 ? 'bg-ok-soft text-ok-strong' : kind === -1 ? 'bg-danger-soft text-danger-strong line-through' : ''}>{text}</span>)}
  </div>
}

const versionSourceLabel: Record<string, string> = { manual: '手动保存', import: '导入', revised: '版本恢复', auto: '自动' }

export function ReaderPage() {
  const { currentBook, chapters, setChapters, currentChapter, setCurrentChapter } = useAppStore()
  const prefs = useUiPrefs()
  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [versions, setVersions] = useState<Version[]>([])
  const [saving, setSaving] = useState(false)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryBatchLoading, setSummaryBatchLoading] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [focusMode, setFocusMode] = useState(false)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [targetChapterNumber, setTargetChapterNumber] = useState<number | null>(null)
  const [composedPrompt, setComposedPrompt] = useState('')
  const [promptTargetNumber, setPromptTargetNumber] = useState<number | null>(null)
  const [promptLoading, setPromptLoading] = useState(false)
  const [promptCopied, setPromptCopied] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)
  const [diffPair, setDiffPair] = useState<{ left: number | null; right: number | null }>({ left: null, right: null })
  const [showDiff, setShowDiff] = useState(false)
  const [restoring, setRestoring] = useState<Version | null>(null)
  const [restoreBusy, setRestoreBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [pendingSwitch, setPendingSwitch] = useState<Chapter | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const readScrollRef = useRef<HTMLDivElement>(null)
  const positionTimer = useRef<number | undefined>(undefined)

  const dirty = !!currentChapter && (content !== currentChapter.current_content || title !== currentChapter.title)
  const editing = prefs.readerMode === 'edit'
  const currentVersion = versions.find(v => v.id === currentChapter?.current_version_id) || versions[0]

  /* ---------- 章节切换（含未保存保护） ---------- */

  const applyChapter = async (chapter: Chapter, availableChapters = chapters) => {
    setCurrentChapter(chapter)
    setContent(chapter.current_content)
    setTitle(chapter.title)
    setTargetChapterNumber(Math.min(chapter.chapter_number + 1, Math.max(...availableChapters.map(item => item.chapter_number), 0) + 1))
    setError(''); setDiffPair({ left: null, right: null }); setShowDiff(false)
    try { setVersions(await api<Version[]>(`/chapters/${chapter.id}/versions`)) } catch { setVersions([]) }
  }
  const requestChapter = (chapter: Chapter) => {
    if (chapter.id === currentChapter?.id) return
    if (dirty) setPendingSwitch(chapter)
    else void applyChapter(chapter)
  }
  useEffect(() => { if (chapters.length && !currentChapter) void applyChapter(chapters[0]) }, [chapters])
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])

  /* 阅读模式下恢复并持续记录阅读位置 */
  useEffect(() => {
    if (!editing && readScrollRef.current && currentChapter) readScrollRef.current.scrollTop = currentChapter.reading_position || 0
  }, [currentChapter?.id, editing])
  const recordPosition = () => {
    if (editing || !currentChapter) return
    window.clearTimeout(positionTimer.current)
    positionTimer.current = window.setTimeout(() => {
      const top = readScrollRef.current?.scrollTop ?? 0
      void api(`/chapters/${currentChapter.id}`, { method: 'PUT', body: JSON.stringify({ reading_position: Math.round(top) }) }).catch(() => undefined)
    }, 800)
  }

  /* ---------- 业务操作（语义与原实现一致） ---------- */

  const save = async () => {
    if (!currentChapter) return
    setSaving(true); setError('')
    try {
      const version = await api<Version>(`/chapters/${currentChapter.id}/versions`, { method: 'POST', body: JSON.stringify({ content, note: '阅读器手动保存' }) })
      await api(`/chapters/${currentChapter.id}`, { method: 'PUT', body: JSON.stringify({ title }) })
      const updated = { ...currentChapter, title, current_content: content, word_count: version.word_count, current_version_id: version.id }
      setCurrentChapter(updated); setChapters(chapters.map(item => item.id === updated.id ? updated : item))
      setVersions([version, ...versions])
      toast(`已保存为 v${version.version_number}，旧版本可恢复`)
    } catch (exc) { setError(String(exc)); toast('保存失败', 'danger') } finally { setSaving(false) }
  }
  const createChapter = async () => {
    if (!currentBook) return
    const chapter = await api<Chapter>(`/books/${currentBook.id}/chapters`, { method: 'POST', body: JSON.stringify({ title: `第${chapters.length + 1}章`, content: '' }) })
    const next = [...chapters, chapter]; setChapters(next); void applyChapter(chapter, next)
    toast(`已创建「${chapter.title}」`)
  }
  const importFile = async (file: File) => {
    if (!currentBook) return
    try {
      const added = await upload<Chapter[]>(`/books/${currentBook.id}/chapters/import`, file)
      const next = [...chapters, ...added]; setChapters(next); void applyChapter(added[0], next)
      toast(`已导入 ${added.length} 个章节`)
    } catch (exc) { setError(String(exc)); toast('导入失败', 'danger') }
  }
  const removeChapter = async () => {
    if (!currentChapter) return
    setDeleteBusy(true)
    try {
      await api(`/chapters/${currentChapter.id}`, { method: 'DELETE' })
      const next = chapters.filter(item => item.id !== currentChapter!.id)
      setChapters(next); setDeleting(false); setCurrentChapter(null)
      if (next[0]) void applyChapter(next[0], next)
      toast('章节已删除')
    } catch (exc) { setError(String(exc)) } finally { setDeleteBusy(false) }
  }
  const restoreVersion = async () => {
    if (!currentChapter || !restoring) return
    setRestoreBusy(true)
    try {
      const version = await api<Version>(`/chapters/${currentChapter.id}/versions/${restoring.id}/restore`, { method: 'POST' })
      const updated = { ...currentChapter, current_content: version.content, word_count: version.word_count, current_version_id: version.id }
      setCurrentChapter(updated); setChapters(chapters.map(item => item.id === updated.id ? updated : item))
      setContent(version.content); setVersions([version, ...versions]); setRestoring(null)
      toast(`已从 v${restoring.version_number} 恢复，生成新版本 v${version.version_number}`)
    } catch (exc) { setError(String(exc)) } finally { setRestoreBusy(false) }
  }
  const updateSummary = async () => {
    if (!currentChapter) return
    setSummaryLoading(true); setError('')
    try {
      const result = await api<ChapterSummaryResult>(`/chapters/${currentChapter.id}/summary`, { method: 'POST' })
      const updated = { ...currentChapter, summary: result.summary, summary_content_sha256: result.content_sha256, summary_source_version_id: result.source_version_id, summary_model: result.model, summary_updated_at: result.updated ? new Date().toISOString() : currentChapter.summary_updated_at }
      setCurrentChapter(updated); setChapters(chapters.map(item => item.id === updated.id ? updated : item))
      if (result.status === 'failed') { setError(result.message); toast('梗概更新失败', 'danger') } else toast(result.message, 'accent')
    } catch (exc) { setError(String(exc)); toast('梗概更新失败', 'danger') } finally { setSummaryLoading(false) }
  }
  const updateAllSummaries = async () => {
    if (!currentBook) return
    setSummaryBatchLoading(true); setError('')
    try {
      const result = await api<ChapterSummaryBatchResult>(`/books/${currentBook.id}/chapter-summaries`, { method: 'POST' })
      const refreshed = await api<Chapter[]>(`/books/${currentBook.id}/chapters`); setChapters(refreshed)
      const selected = currentChapter ? refreshed.find(item => item.id === currentChapter.id) : null
      if (selected) setCurrentChapter(selected)
      toast(`批量梗概完成：更新 ${result.updated} 章，跳过 ${result.skipped} 章，失败 ${result.failed} 章`, 'accent')
    } catch (exc) { setError(String(exc)); toast('批量梗概更新失败', 'danger') } finally { setSummaryBatchLoading(false) }
  }
  const analyze = async () => {
    if (!currentChapter) return
    setAnalyzing(true); setError('')
    try {
      const result = await api<Analysis>(`/chapters/${currentChapter.id}/analyze`, { method: 'POST', body: JSON.stringify({ analysis_type: 'characters' }) })
      setAnalysis(result)
      if (result.status === 'completed') toast(`角色分析完成（${result.model}），无冲突变更已自动入库`)
      else { setError(result.error_message || 'LLM 未返回可用结果，请查看分析记录。'); toast('角色分析失败', 'danger') }
    } catch (exc) { setError(String(exc)); toast('角色分析请求失败', 'danger') } finally { setAnalyzing(false) }
  }
  const composePrompt = async () => {
    if (!currentChapter) return
    const maxChapterNumber = Math.max(...chapters.map(item => item.chapter_number), 0)
    const target = targetChapterNumber || Math.min(currentChapter.chapter_number + 1, maxChapterNumber + 1)
    setPromptLoading(true); setPromptCopied(false); setError('')
    try {
      const result = await api<{ prompt: string; context: { chapter: { number: number } } }>(`/chapters/${currentChapter.id}/prompt`, { method: 'POST', body: JSON.stringify({ target_chapter_number: target }) })
      setComposedPrompt(result.prompt); setPromptTargetNumber(result.context.chapter.number); setShowPrompt(true)
    } catch (exc) { setError(String(exc)); toast('提示词组合失败', 'danger') } finally { setPromptLoading(false) }
  }
  const copyPrompt = async () => {
    if (!composedPrompt) return
    try { await navigator.clipboard.writeText(composedPrompt) } catch {
      const textarea = document.createElement('textarea'); textarea.value = composedPrompt; textarea.style.position = 'fixed'; textarea.style.opacity = '0'
      document.body.appendChild(textarea); textarea.select(); document.execCommand('copy'); textarea.remove()
    }
    setPromptCopied(true); toast('提示词已复制到剪贴板')
  }
  const reviewChange = async (change: Analysis['changes'][number], type: 'accept' | 'reject') => {
    if (!analysis) return
    try { await api(`/analysis/${analysis.id}/changes/${change.id}/${type}`, { method: 'POST' }); setAnalysis(await api<Analysis>(`/analysis/${analysis.id}`)) } catch (exc) { setError(String(exc)) }
  }

  const diffVersions = diffPair.left && diffPair.right ? { left: versions.find(v => v.id === diffPair.left), right: versions.find(v => v.id === diffPair.right) } : null
  const visible = chapters.filter(item => `${item.title}${item.chapter_number}`.includes(search))
  const showChapters = prefs.readerShowChapters && !focusMode
  const showContext = prefs.readerShowContext && !focusMode

  if (chapters.length === 0) return <div className="flex h-full items-center justify-center p-8">
    <Empty icon={<BookOpenText size={24} />} title="这本书还没有章节" text="新建第一章开始写作，或导入已有的 TXT / Markdown 正文。" action={
      <div className="flex gap-2">
        <input ref={fileInput} type="file" accept=".txt,.md,.markdown" className="hidden" onChange={e => e.target.files?.[0] && void importFile(e.target.files[0])} />
        <Button variant="secondary" onClick={() => fileInput.current?.click()}><Upload size={15} />导入正文</Button>
        <Button variant="primary" onClick={() => void createChapter()}><FilePlus2 size={15} />新建第一章</Button>
      </div>} />
  </div>

  return <div className="flex h-full min-h-0">
    <input ref={fileInput} type="file" accept=".txt,.md,.markdown" className="hidden" onChange={e => e.target.files?.[0] && void importFile(e.target.files[0])} />

    {/* 左栏：章节列表 */}
    {showChapters && <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-paper-raised" aria-label="章节列表">
      <div className="border-b border-line-soft p-3">
        <div className="flex items-center justify-between">
          <SectionTitle>章节 · {chapters.length}</SectionTitle>
          <Button size="sm" variant="subtle" onClick={() => void createChapter()}><FilePlus2 size={13} />新建</Button>
        </div>
        <div className="relative mt-2.5">
          <ListFilter size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
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
        <Button variant="ghost" size="sm" className="w-full" disabled={summaryBatchLoading} onClick={() => void updateAllSummaries()}>{summaryBatchLoading ? '批量更新梗概中…' : '批量更新章节梗概'}</Button>
        <Button variant="ghost" size="sm" className="mt-1 w-full" onClick={() => fileInput.current?.click()}><Upload size={13} />导入 TXT / Markdown</Button>
      </div>
    </aside>}

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
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1 whitespace-nowrap text-meta text-ink-faint">
            <span>提示词目标</span>
            <Select aria-label="提示词目标章节" className="w-auto py-1 text-meta" value={targetChapterNumber || ''} onChange={e => setTargetChapterNumber(Number(e.target.value))}>
              {chapters.map(chapter => <option key={chapter.id} value={chapter.chapter_number}>第 {chapter.chapter_number} 章</option>)}
              {chapters.length > 0 && <option value={Math.max(...chapters.map(item => item.chapter_number)) + 1}>第 {Math.max(...chapters.map(item => item.chapter_number)) + 1} 章 · 待创作</option>}
            </Select>
          </label>
          <Button size="sm" variant="secondary" disabled={saving || !currentChapter} onClick={() => void save()}><Save size={14} />{saving ? '保存中…' : '保存新版本'}</Button>
          <Button size="sm" variant="secondary" disabled={promptLoading || !currentChapter} onClick={() => void composePrompt()}><Clipboard size={14} />{promptLoading ? '组合中…' : '组合提示词'}</Button>
          <Button size="sm" variant="secondary" disabled={analyzing || !currentChapter} onClick={() => void analyze()}><Sparkles size={14} />{analyzing ? '分析中…' : '分析本章角色'}</Button>
          <Button size="sm" variant="ghost" title="上下文栏" aria-label="切换上下文栏" onClick={() => { if (focusMode) setFocusMode(false); else prefs.toggleReaderContext() }}><PanelRight size={15} /></Button>
        </div>
      </div>
      {error && <div className="shrink-0 px-4 pt-3"><InlineError message={error} /></div>}

      {/* 正文区 */}
      {currentChapter ? (
        editing ? <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-thin">
          <div className="mx-auto w-full max-w-measure flex-1 px-6 py-6">
            <Input className="border-transparent bg-transparent px-0 font-serif text-lg font-semibold focus:border-line" value={title} onChange={e => setTitle(e.target.value)} aria-label="章节标题" placeholder="章节标题" />
            <Textarea className="paper-text mt-3 min-h-[60vh] resize-y border-0 bg-transparent px-0 shadow-none focus:border-transparent" style={{ fontSize: prefs.readerFontSize }} value={content} onChange={e => setContent(e.target.value)} placeholder="从这里开始写正文，或导入已有章节…" aria-label="章节正文" />
          </div>
        </div>
        : <div ref={readScrollRef} onScroll={recordPosition} className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
          <article className="mx-auto max-w-measure px-6 py-8">
            <MetaText>第 {currentChapter.chapter_number} 章</MetaText>
            <h1 className="mt-1 font-serif text-2xl font-bold text-ink">{currentChapter.title}</h1>
            <div className="mt-6 paper-text" style={{ fontSize: prefs.readerFontSize }}>{currentChapter.current_content || '本章还没有正文。切换到「编辑」开始写作。'}</div>
          </article>
        </div>
      ) : <div className="flex flex-1 items-center justify-center"><MetaText>从左侧选择一章</MetaText></div>}

      {/* 状态栏 */}
      {currentChapter && <div className="flex shrink-0 items-center justify-between border-t border-line bg-paper-raised px-4 py-1.5 text-meta tabular-nums text-ink-faint">
        <span>{editing ? `编辑中 · ${countWords(content).toLocaleString()} 字` : `${currentChapter.word_count.toLocaleString()} 字`}{currentVersion ? ` · 当前 v${currentVersion.version_number}` : ''}</span>
        <span>{dirty ? '修改尚未保存，切换章节前会提醒你' : '所有修改已保存为版本'}</span>
      </div>}
    </div>

    {/* 右栏：上下文（窄屏自动收纳，可通过右上角按钮在宽屏重新展开） */}
    {showContext && currentChapter && <aside className="hidden w-72 shrink-0 flex-col overflow-y-auto border-l border-line bg-paper-raised scrollbar-thin xl:flex" aria-label="章节上下文">
      <div className="space-y-4 p-3.5">
        <Panel title="本章梗概" flush={false} action={<Button size="sm" variant="subtle" disabled={summaryLoading || summaryBatchLoading} onClick={() => void updateSummary()}>{summaryLoading ? '更新中…' : '更新梗概'}</Button>}>
          <p className="whitespace-pre-wrap text-[13px] leading-6 text-ink-soft">{currentChapter.summary || '尚未生成梗概。更新梗概会参考当前正文；批量更新会跳过正文未变化的章节。'}</p>
          {currentChapter.summary_updated_at && <MetaText className="mt-2">{currentChapter.summary_model || '未记录模型'} · {new Date(currentChapter.summary_updated_at).toLocaleString()}</MetaText>}
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
                {version.note && <MetaText className="mt-0.5">{version.note}</MetaText>}
                {!isCurrent && <Button size="sm" variant="ghost" className="mt-1 px-1.5 text-accent-700" onClick={() => setRestoring(version)}><RotateCcw size={12} />恢复到此版本</Button>}
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
        <Button variant="danger" onClick={() => { const target = pendingSwitch; setPendingSwitch(null); if (target) void applyChapter(target) }}>放弃修改并切换</Button>
        <Button variant="primary" disabled={saving} onClick={async () => { await save(); const target = pendingSwitch; setPendingSwitch(null); if (target) void applyChapter(target) }}><Save size={14} />保存并切换</Button>
      </>}>
      <p className="text-body leading-7 text-ink-soft">「{currentChapter?.title}」的正文或标题已修改但尚未保存为版本。保存会生成一个新版本；放弃则丢失本次未保存的修改。</p>
    </Modal>

    {/* 版本恢复确认 */}
    <ConfirmModal open={!!restoring} onClose={() => setRestoring(null)} onConfirm={() => void restoreVersion()} busy={restoreBusy}
      title={`恢复到 v${restoring?.version_number}？`} confirmText="恢复为新版本"
      description={<>不会覆盖任何历史版本：系统会把 v{restoring?.version_number} 的内容复制为一个<strong>新版本</strong>并设为当前版本，随后照常触发自动分析。</>} />

    {/* 删除章节确认 */}
    <ConfirmModal open={deleting} onClose={() => setDeleting(false)} onConfirm={() => void removeChapter()} danger busy={deleteBusy}
      title={`删除「${currentChapter?.title}」？`} confirmText="永久删除"
      description={<>将删除本章及其全部 {versions.length} 个历史版本，<strong className="text-danger-strong">不可撤销</strong>。</>} />

    {/* 版本对比 */}
    <Modal open={showDiff} onClose={() => setShowDiff(false)} title="版本对比" description="选择两个版本查看字符级差异：红色删除线是被移除的内容，绿色是新增内容。" width="max-w-4xl"
      footer={<Button variant="ghost" onClick={() => setShowDiff(false)}>关闭</Button>}>
      <div className="mb-4 grid grid-cols-2 gap-3">
        {(['left', 'right'] as const).map(side => <Field key={side} label={side === 'left' ? '旧版本（左）' : '新版本（右）'}>
          <Select value={diffPair[side] || ''} onChange={e => setDiffPair({ ...diffPair, [side]: Number(e.target.value) || null })}>
            <option value="">选择版本</option>
            {versions.map(version => <option key={version.id} value={version.id}>v{version.version_number} · {new Date(version.created_at).toLocaleDateString()}</option>)}
          </Select>
        </Field>)}
      </div>
      {diffVersions?.left && diffVersions?.right
        ? <DiffView left={diffVersions.left.content} right={diffVersions.right.content} />
        : <Empty compact title="请选择两个版本" text="通常左边选较早的版本，右边选较晚的版本。" />}
    </Modal>

    {/* 组合提示词 */}
    <Modal open={showPrompt} onClose={() => setShowPrompt(false)} width="max-w-4xl"
      title={`第 ${promptTargetNumber} 章组合提示词`} description="聚合了大纲位置、前文梗概与角色状态，可复制到其他 LLM 用于生成目标章节。"
      footer={<>
        <MetaText className="mr-auto">{composedPrompt.length.toLocaleString()} 字符{promptCopied ? ' · 已复制' : ''}</MetaText>
        <Button variant="ghost" onClick={() => setShowPrompt(false)}>关闭</Button>
        <Button variant="primary" onClick={() => void copyPrompt()}><Copy size={14} />{promptCopied ? '再次复制' : '复制提示词'}</Button>
      </>}>
      <Textarea className="min-h-[55vh] resize-y bg-paper font-mono text-[13px] leading-6" value={composedPrompt} readOnly aria-label="组合提示词内容" />
    </Modal>

    {/* 本章角色分析 */}
    <Modal open={!!analysis} onClose={() => setAnalysis(null)} width="max-w-4xl"
      title={`第 ${currentChapter?.chapter_number} 章角色分析`}
      description={analysis ? `模型：${analysis.model} · 状态：${statusText[analysis.status] || analysis.status} · 耗时 ${analysis.elapsed_ms}ms。无冲突变更已自动入库，此处可处理待确认项。` : undefined}
      footer={<Button variant="ghost" onClick={() => setAnalysis(null)}>关闭</Button>}>
      {analysis && <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-md bg-paper-sunken px-3.5 py-3"><MetaText>本章出现角色</MetaText><div className="mt-1 text-xl font-bold tabular-nums">{analysis.result_json?.characters?.length || 0}</div></div>
          <div className="rounded-md bg-paper-sunken px-3.5 py-3"><MetaText>待确认变更</MetaText><div className="mt-1 text-xl font-bold tabular-nums">{analysis.changes?.length || 0}</div></div>
        </div>
        {analysis.result_json?.notice && <InlineNotice tone="warn" message={analysis.result_json.notice} />}
        <AnalysisResultDetails analysis={analysis} onChangeAction={reviewChange} />
      </div>}
    </Modal>

  </div>
}
