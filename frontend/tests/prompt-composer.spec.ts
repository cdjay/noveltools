import { test, expect } from '@playwright/test'

test('阅读器可以选择目标章节并组合可复制提示词', async ({ page }) => {
  const book = {
    id: 1,
    title: '提示词测试书',
    author: '',
    description: '',
    genre: '奇幻',
    status: 'draft',
    current_chapter_id: 1,
    chapter_count: 11,
    total_word_count: 1100,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
  const chapters = Array.from({ length: 11 }, (_, index) => ({
    id: index + 1,
    book_id: 1,
    chapter_number: index + 1,
    title: `第${index + 1}章`,
    status: 'draft',
    current_version_id: index + 1,
    word_count: 100,
    reading_position: 0,
    current_content: `第${index + 1}章正文`,
    updated_at: '2026-01-01T00:00:00Z',
  }))

  await page.route('**/api/books/1', route => route.fulfill({ json: book }))
  await page.route('**/api/books/1/chapters', route => route.fulfill({ json: chapters }))
  await page.route('**/api/chapters/*/versions', route => route.fulfill({ json: [{ id: 1, chapter_id: 1, version_number: 1, content: '正文', word_count: 2, source: 'manual', note: '', created_at: '2026-01-01T00:00:00Z' }] }))
  await page.route('**/api/chapters/1/prompt', async route => {
    const body = route.request().postDataJSON()
    expect(body.target_chapter_number).toBe(12)
    await route.fulfill({ json: { prompt: '# 第12章组合提示词\n## 前两章全文\n第10章\n第11章', context: { chapter: { number: 12 } } } })
  })

  await page.goto('/books/1/reader')
  await expect(page.getByText('第1章').first()).toBeVisible()
  await page.getByLabel('提示词目标章节').selectOption('12')
  await page.getByRole('button', { name: '组合提示词' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('第 12 章组合提示词')).toBeVisible()
  await expect(page.locator('textarea[readonly]')).toHaveValue(/第12章组合提示词/)
})
