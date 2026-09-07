import { useEffect, useState } from 'react'
import { BookOpenText, ChevronLeft, FilePlus2, LibraryBig, ListTree, PanelLeftClose, PanelLeftOpen, PenLine, Search, Settings } from 'lucide-react'
import { Routes, Route, Navigate, Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, API_BASE } from './api/client'
import { useAppStore } from './stores/useAppStore'
import { useUiPrefs } from './stores/useUiPrefs'
import type { Book, Chapter } from './types'
import { Badge, Button, ConfirmModal, Empty, Field, InlineError, Input, Loading, Modal, PageHeader, Select, Textarea, toast, ToastHost } from './lib/ui'
import { ReaderPage } from './pages/ReaderPage'
import { OutlinePage } from './pages/OutlinePage'
import { SettingsPage } from './pages/SettingsPage'

const bookStatusLabel: Record<string, string> = { draft: '草稿', ongoing: '连载', completed: '已完成' }
const bookStatusTone = (status: string) => status === 'completed' ? 'ok' as const : status === 'ongoing' ? 'accent' as const : 'neutral' as const

/* ================= 书架 ================= */

function BookFormModal({ open, onClose, book, onSaved }: { open: boolean; onClose: () => void; book: Book | null; onSaved: (book: Book) => void }) {
  const [form, setForm] = useState({ title: '', author: '', genre: '', status: 'draft', description: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (open) { setForm(book ? { title: book.title, author: book.author, genre: book.genre, status: book.status, description: book.description } : { title: '', author: '', genre: '', status: 'draft', description: '' }); setError('') }
  }, [open, book])
  const save = async () => {
    if (!form.title.trim()) { setError('请填写书名'); return }
    setBusy(true); setError('')
    try {
      const saved = book
        ? await api<Book>(`/books/${book.id}`, { method: 'PUT', body: JSON.stringify(form) })
        : await api<Book>('/books', { method: 'POST', body: JSON.stringify(form) })
      onSaved(saved); onClose(); toast(book ? '书籍信息已更新' : `《${saved.title}》已创建`)
    } catch (exc) { setError(String(exc)) } finally { setBusy(false) }
  }
  return <Modal open={open} onClose={onClose} title={book ? `编辑《${book.title}》` : '新建书籍'} description="书名必填，其余信息可以随时补充。"
    footer={<><Button variant="ghost" onClick={onClose}>取消</Button><Button variant="primary" disabled={busy} onClick={() => void save()}>{busy ? '保存中…' : book ? '保存修改' : '创建书籍'}</Button></>}>
    <div className="space-y-4">
      {error && <InlineError message={error} />}
      <Field label="书名"><Input autoFocus value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} onKeyDown={e => e.key === 'Enter' && void save()} placeholder="例如：长安十二时辰" /></Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="作者"><Input value={form.author} onChange={e => setForm({ ...form, author: e.target.value })} placeholder="笔名" /></Field>
        <Field label="类型"><Input value={form.genre} onChange={e => setForm({ ...form, genre: e.target.value })} placeholder="玄幻 / 悬疑 / 科幻…" /></Field>
      </div>
      <Field label="状态">
        <Select value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>
          <option value="draft">草稿</option><option value="ongoing">连载中</option><option value="completed">已完成</option>
        </Select>
      </Field>
      <Field label="简介"><Textarea rows={3} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="一句话故事前提，帮助自己快速进入状态" /></Field>
    </div>
  </Modal>
}

function LibraryPage() {
  const { books, setBooks, setCurrentBook } = useAppStore()
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Book | null>(null)
  const [removing, setRemoving] = useState<Book | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const load = async () => {
    setError('')
    try { setBooks(await api<Book[]>('/books')) } catch (exc) { setError(String(exc)) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  const remove = async () => {
    if (!removing) return
    setRemoveBusy(true)
    try {
      await api(`/books/${removing.id}`, { method: 'DELETE' })
      setBooks(books.filter(item => item.id !== removing.id)); setCurrentBook(null)
      toast(`《${removing.title}》已删除`); setRemoving(null)
    } catch (exc) { setError(String(exc)) } finally { setRemoveBusy(false) }
  }
  const filtered = books.filter(book => `${book.title}${book.author}${book.genre}`.toLowerCase().includes(query.toLowerCase()))
  return <div className="min-h-screen bg-paper">
    <header className="border-b border-line bg-paper-raised">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5 lg:px-10">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-accent-700 text-white"><LibraryBig size={19} /></span>
          <div>
            <div className="text-[17px] font-bold tracking-tight text-ink">小说写作工作台</div>
            <div className="text-meta text-ink-faint">专注正文与大纲 · 内容保存在本机</div>
          </div>
        </div>
        <Button variant="primary" onClick={() => { setEditing(null); setFormOpen(true) }}><FilePlus2 size={15} />新建书籍</Button>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-6 py-8 lg:px-10">
      <PageHeader title="我的书架" description="每本书的正文、大纲与历史版本独立保存。" actions={
        books.length > 0 ? <div className="relative">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" />
          <Input className="w-64 pl-8" placeholder="搜索书名、作者或类型" value={query} onChange={e => setQuery(e.target.value)} aria-label="搜索书籍" />
        </div> : undefined
      } />
      <div className="mt-6 space-y-4">
        {error && <InlineError message={error} onRetry={() => void load()} />}
        {loading ? <Loading text="正在打开书架…" /> : filtered.length === 0 ? (
          query ? <Empty icon={<Search size={22} />} title={`没有找到与“${query}”相关的书`} text="换个关键词试试。" action={<Button variant="ghost" onClick={() => setQuery('')}>清空搜索</Button>} />
            : <Empty icon={<BookOpenText size={22} />} title="书架上还没有书" text="新建一本书，从第一章开始写作。" action={<Button variant="primary" onClick={() => { setEditing(null); setFormOpen(true) }}><FilePlus2 size={15} />新建第一本书</Button>} />
        ) : <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map(book => <article key={book.id} className="group flex flex-col rounded-lg border border-line bg-paper-raised p-5 shadow-card transition-shadow duration-fast hover:shadow-pop">
            <div className="flex items-start justify-between gap-3">
              <h3 className="min-w-0 flex-1 truncate font-serif text-lg font-semibold text-ink" title={book.title}>{book.title}</h3>
              <Badge tone={bookStatusTone(book.status)}>{bookStatusLabel[book.status] || book.status}</Badge>
            </div>
            <div className="mt-1 text-meta text-ink-faint">{[book.author, book.genre].filter(Boolean).join(' · ') || '未填写作者与类型'}</div>
            <p className="mt-3 line-clamp-2 min-h-10 text-body leading-6 text-ink-soft">{book.description || '还没有简介。'}</p>
            <div className="mt-4 flex gap-6 border-t border-line-soft pt-3 text-meta text-ink-faint">
              <span><span className="font-semibold tabular-nums text-ink-soft">{book.chapter_count}</span> 章</span>
              <span><span className="font-semibold tabular-nums text-ink-soft">{book.total_word_count.toLocaleString()}</span> 字</span>
              <span className="ml-auto">更新于 {new Date(book.updated_at).toLocaleDateString()}</span>
            </div>
            <div className="mt-4 flex gap-2">
              <Button variant="primary" className="flex-1" onClick={() => { setCurrentBook(book); navigate(`/books/${book.id}/reader`) }}>进入工作区</Button>
              <Button variant="ghost" onClick={() => { setEditing(book); setFormOpen(true) }}>编辑信息</Button>
              <Button variant="ghost" className="text-danger hover:bg-danger-soft hover:text-danger-strong" aria-label={`删除《${book.title}》`} onClick={() => setRemoving(book)}>删除</Button>
            </div>
          </article>)}
        </div>}
      </div>
    </main>
    <BookFormModal open={formOpen} onClose={() => setFormOpen(false)} book={editing} onSaved={saved => setBooks(editing ? books.map(item => item.id === saved.id ? { ...item, ...saved } : item) : [saved, ...books])} />
    <ConfirmModal open={!!removing} onClose={() => setRemoving(null)} onConfirm={() => void remove()} danger busy={removeBusy}
      title={`删除《${removing?.title}》？`} confirmText="永久删除"
      description={<>将删除这本书的全部内容及历史版本，<strong className="text-danger-strong">此操作不可撤销</strong>。删除前请确认已备份本机数据。</>} />
  </div>
}

/* ================= 书籍工作区 ================= */

type NavItem = { key: string; label: string; icon: typeof PenLine }
const navGroups: { title: string; items: NavItem[] }[] = [
  { title: '写作', items: [{ key: 'reader', label: '正文', icon: PenLine }, { key: 'outline', label: '大纲', icon: ListTree }] },
  { title: '管理', items: [{ key: 'settings', label: 'LLM 配置', icon: Settings }] },
]

function WorkspaceLayout() {
  const { bookId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const { currentBook, setCurrentBook, setChapters } = useAppStore()
  const { navCollapsed, toggleNav } = useUiPrefs()
  const [error, setError] = useState('')
  const loadWorkspace = async () => {
    if (!bookId) return
    setError('')
    try {
      const [book, chapters] = await Promise.all([api<Book>(`/books/${bookId}`), api<Chapter[]>(`/books/${bookId}/chapters`)])
      setCurrentBook(book); setChapters(chapters)
    } catch (exc) { setError(String(exc)) }
  }
  useEffect(() => { void loadWorkspace() }, [bookId])
  const activeKey = navGroups.flatMap(group => group.items).find(item => location.pathname.endsWith(`/${item.key}`))?.key || 'reader'
  const activeLabel = navGroups.flatMap(group => group.items).find(item => item.key === activeKey)?.label || '正文'

  if (error) return <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-paper px-6">
    <InlineError message={`打开书籍工作区失败：${error}`} onRetry={() => void loadWorkspace()} />
    <Button variant="ghost" onClick={() => navigate('/')}><ChevronLeft size={15} />返回书架</Button>
  </div>
  if (!currentBook || String(currentBook.id) !== bookId) return <div className="min-h-screen bg-paper"><Loading text="正在打开书籍工作区…" /></div>

  return <div className="flex h-screen overflow-hidden bg-paper">
    <aside className={`flex shrink-0 flex-col border-r border-line bg-paper-raised transition-[width] duration-150 ${navCollapsed ? 'w-14' : 'w-56'}`} aria-label="工作区导航">
      <div className={`border-b border-line-soft px-3 py-3 ${navCollapsed ? 'flex justify-center' : ''}`}>
        {navCollapsed
          ? <Link to="/" title="返回书架" className="flex h-8 w-8 items-center justify-center rounded-md text-ink-soft transition-colors hover:bg-paper-sunken"><LibraryBig size={17} /></Link>
          : <div className="min-w-0">
            <Link to="/" className="inline-flex items-center gap-1 text-meta text-ink-faint transition-colors hover:text-accent-700"><ChevronLeft size={13} />书架</Link>
            <div className="mt-1 truncate font-serif text-[15px] font-semibold text-ink" title={currentBook.title}>{currentBook.title}</div>
            <div className="mt-0.5 text-meta tabular-nums text-ink-faint">{currentBook.chapter_count} 章 · {currentBook.total_word_count.toLocaleString()} 字</div>
          </div>}
      </div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3 scrollbar-thin">
        {navGroups.map(group => <div key={group.title} className="mb-4">
          {!navCollapsed && <div className="px-2 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-ink-faint">{group.title}</div>}
          {group.items.map(item => {
            const active = activeKey === item.key
            const Icon = item.icon
            return <button key={item.key} title={item.label} aria-current={active ? 'page' : undefined}
              className={`mb-0.5 flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-body transition-colors duration-fast ${active ? 'bg-accent-50 font-medium text-accent-700' : 'text-ink-soft hover:bg-paper-sunken hover:text-ink'} ${navCollapsed ? 'justify-center px-0' : ''}`}
              onClick={() => navigate(`/books/${bookId}/${item.key}`)}>
              <Icon size={16} className="shrink-0" />{!navCollapsed && item.label}
            </button>
          })}
        </div>)}
      </nav>
      <div className="border-t border-line-soft p-2">
        <button onClick={toggleNav} title={navCollapsed ? '展开导航' : '收起导航'} className="flex w-full items-center justify-center gap-2 rounded-md px-2 py-1.5 text-meta text-ink-faint transition-colors hover:bg-paper-sunken hover:text-ink">
          {navCollapsed ? <PanelLeftOpen size={15} /> : <><PanelLeftClose size={15} />收起导航</>}
        </button>
        {!navCollapsed && <div className="px-2 pb-1 pt-0.5 text-center text-[11px] text-ink-faint/70">本地模式 · 数据不出本机</div>}
      </div>
    </aside>
    <main className="flex min-w-0 flex-1 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-line bg-paper-raised px-5">
        <div className="flex min-w-0 items-center gap-2 text-body">
          <span className="truncate font-serif font-semibold text-ink">{currentBook.title}</span>
          <span className="text-ink-faint">/</span>
          <span className="shrink-0 text-ink-soft">{activeLabel}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <a href={`${API_BASE}/books/${bookId}/export.txt`} className="text-meta text-ink-faint transition-colors hover:text-accent-700" download>导出 TXT</a>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        <Routes>
          <Route path="reader" element={<ReaderPage key={bookId} />} />
          <Route path="outline" element={<OutlinePage key={bookId} />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to={`/books/${bookId}/reader`} replace />} />
        </Routes>
      </div>
    </main>
  </div>
}

export default function App() {
  return <>
    <Routes>
      <Route path="/" element={<LibraryPage />} />
      <Route path="/books/:bookId/*" element={<WorkspaceLayout />} />
    </Routes>
    <ToastHost />
  </>
}
