import { test, expect } from '@playwright/test'

test('角色分析展示本章角色和角色卡确认入口', async ({ page }) => {
  const book = {
    id: 1,
    title: '测试书',
    author: '',
    description: '',
    genre: '仙侠',
    status: 'draft',
    current_chapter_id: 1,
    chapter_count: 1,
    total_word_count: 1200,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
  const chapter = {
    id: 1,
    book_id: 1,
    chapter_number: 1,
    title: '第一章',
    status: 'draft',
    current_version_id: 1,
    word_count: 1200,
    reading_position: 0,
    current_content: '陈渡发现了炉底泄压口。',
    updated_at: '2026-01-01T00:00:00Z',
  }
  const analysis = {
    id: 101,
    book_id: 1,
    chapter_id: 1,
    status: 'completed',
    model: 'test-model',
    elapsed_ms: 120,
    error_message: '',
    raw_response: '{}',
    created_at: '2026-01-01T00:00:00Z',
    result_json: {
      analysis_type: 'characters',
      characters: [{ name: '陈渡', match_status: 'existing', appearance: '右臂缠着旧护腕', level: '练气一层', evidence: [{ text: '陈渡抬起右臂', position: 'paragraph_2' }], confidence: 0.94, field_changes: [{ field: 'level', label: '等级 / 境界', old_value: '练气一层', new_value: '练气二层' }] }],
      character_changes: [],
    },
    changes: [{ id: 201, analysis_id: 101, change_type: 'character_update', target_type: 'character', target_name: '陈渡', old_value: { level: '练气一层' }, proposed_value: { level: '练气二层' }, reason: '本章出现了新信息', evidence: '陈渡抬起右臂', confidence: 0.9, conflict: false, status: 'pending' }],
  }

  await page.route('**/api/books/1', route => route.fulfill({ json: book }))
  await page.route('**/api/books/1/chapters', route => route.fulfill({ json: [chapter] }))
  await page.route('**/api/chapters/1/analysis', route => route.fulfill({ json: [analysis] }))

  await page.goto('/books/1/reader')
  await expect(page.getByText('第一章').first()).toBeVisible()
  await page.getByRole('button', { name: '分析记录' }).click()
  await expect(page.getByText('分析 #101')).toBeVisible()
  await page.getByText('分析 #101').click()

  await expect(page.getByText('陈渡', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('右臂缠着旧护腕', { exact: true })).toBeVisible()
  await expect(page.getByText('练气一层', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('已有角色', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '接受写入' })).toBeVisible()
})
