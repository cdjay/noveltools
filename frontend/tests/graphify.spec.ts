import { test, expect } from '@playwright/test'

test('Graphify 工作区可以打开故事状态和初始化提案', async ({ page }) => {
  const book = { id: 1, title: 'Graphify 测试书', author: '', description: '', genre: '奇幻', status: 'draft', current_chapter_id: 1, chapter_count: 1, total_word_count: 30, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }
  const chapter = { id: 1, book_id: 1, chapter_number: 1, title: '开端', status: 'draft', current_version_id: 1, word_count: 30, reading_position: 0, current_content: '陈渡走进火窟。', updated_at: '2026-01-01T00:00:00Z' }
  await page.route('**/api/books/1', route => route.fulfill({ json: book }))
  await page.route('**/api/books/1/chapters', route => route.fulfill({ json: [chapter] }))
  await page.route('**/api/books/1/threads', route => route.fulfill({ json: [] }))
  await page.route('**/api/books/1/graphify/status', route => route.fulfill({ json: { open_threads: [], unresolved_events: [], unresolved_foreshadowing: [], hubs: [], node_count: 2, edge_count: 1 } }))
  await page.route('**/api/books/1/graph', route => route.fulfill({ json: { nodes: [{ id: 'character:1', kind: 'character', label: '陈渡', description: '主角' }, { id: 'knowledge:1', kind: 'knowledge', label: '火窟', description: '地点' }], edges: [{ from: 'character:1', to: 'knowledge:1', relation: '章节引用', note: '' }] } }))
  await page.route('**/api/books/1/change-logs', route => route.fulfill({ json: [] }))
  await page.route('**/api/books/1/graphify/init', route => route.fulfill({ json: { id: 1, book_id: 1, operation: 'init', status: 'completed', model: 'local-safe-mock', elapsed_ms: 1, error_message: '', raw_response: '{}', request_context: {}, result_json: { analysis_type: 'graphify_init', proposals: [] }, proposals: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' } }))
  await page.goto('/books/1/graphify')
  await expect(page.getByRole('heading', { name: '故事图谱' })).toBeVisible()
  await page.getByRole('tab', { name: '故事状态' }).click()
  await expect(page.getByText('图谱节点 / 关系')).toBeVisible()
  await page.getByRole('tab', { name: '关系图' }).click()
  await expect(page.getByRole('heading', { name: '关系图', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '入库日志' }).click()
  await expect(page.getByRole('heading', { name: '入库日志', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '初始化资料库' }).click()
  await page.getByRole('button', { name: '运行初始化' }).click()
  await expect(page.getByText('运行 #1')).toBeVisible()
})
