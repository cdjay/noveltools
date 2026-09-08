import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('正文可手动生成和批量更新梗概，并保护未保存编辑', async ({ page, request }) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '梗概界面验收' } })).json()
  try {
    const chapter = await (await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '初见', content: '沈舟与旧友在城门重逢。' } })).json()
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`/books/${book.id}/reader`)
    let finish: (() => void) | undefined
    let called = false
    const summary = { ...chapter, summary: '【本章概述】沈舟与旧友重逢。', summary_model: '测试模型', summary_updated_at: new Date().toISOString(), summary_source_version_id: chapter.current_version_id, summary_is_stale: false }
    await page.route(`**/chapters/${chapter.id}/summary`, async route => {
      called = true
      await new Promise<void>(resolve => { finish = resolve })
      await route.fulfill({ json: { chapter_id: chapter.id, status: 'completed', updated: true, skipped: false, message: '本章梗概已生成并保存。', chapter: summary } })
    })
    await page.getByRole('button', { name: '生成本章梗概', exact: true }).click()
    await expect.poll(() => called).toBe(true)
    await page.getByRole('button', { name: '编辑', exact: true }).click()
    const editor = page.getByRole('textbox', { name: '章节正文' })
    await editor.fill('梗概生成期间继续编辑的草稿')
    finish!()
    await expect(page.getByLabel('本章梗概内容', { exact: true })).toHaveText(summary.summary)
    await expect(editor).toHaveValue('梗概生成期间继续编辑的草稿')
    await expect(page.getByRole('button', { name: '生成本章梗概', exact: true })).toBeDisabled()
    await expect(page.getByRole('button', { name: '批量更新章节梗概' })).toBeDisabled()
    await editor.fill(chapter.current_content)
    const job = { id: 999, book_id: book.id, status: 'completed', model: '测试模型', total: 1, updated: 0, skipped: 1, failed: 0, items: [] }
    await page.route(`**/books/${book.id}/summary-jobs`, route => route.fulfill({ json: job }))
    await page.route(`**/books/${book.id}/summary-jobs/latest`, route => route.fulfill({ json: job }))
    await page.getByRole('button', { name: '批量更新章节梗概' }).click()
    await expect(page.getByText('梗概更新：成功 0 章，跳过 1 章，失败 0 章')).toBeVisible()
    await page.unroute(`**/chapters/${chapter.id}/summary`)
    await page.getByRole('button', { name: '生成本章梗概', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('LLM 配置')
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
