import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('从书架继续上次章节，恢复草稿并拒绝过时保存', async ({ page, request }) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '阅读恢复验收' } })).json()
  try {
    await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '开篇', content: '第一章正文' } })
    const chapter = await (await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '重逢', content: '第二章正文' } })).json()
    await request.put(`${api}/books/${book.id}`, { data: { current_chapter_id: chapter.id } })
    await page.goto('/')
    await page.getByRole('article').filter({ hasText: book.title }).getByRole('button', { name: '进入工作区' }).click()
    await page.getByRole('button', { name: '编辑', exact: true }).click()
    const editor = page.getByRole('textbox', { name: '章节正文' })
    await expect(editor).toHaveValue('第二章正文')
    await page.getByRole('button', { name: '大纲', exact: true }).click()
    await page.getByRole('button', { name: '正文', exact: true }).click()
    await expect(editor).toHaveValue('第二章正文')
    await editor.fill('浏览器里的草稿')
    page.once('dialog', dialog => dialog.accept())
    await page.reload()
    await expect(editor).toHaveValue('浏览器里的草稿')
    await request.post(`${api}/chapters/${chapter.id}/versions`, { data: { content: '另一窗口的新稿' } })
    await page.getByRole('button', { name: '保存新版本', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('其他窗口更新')
    await expect(editor).toHaveValue('浏览器里的草稿')
    const current = await (await request.get(`${api}/chapters/${chapter.id}`)).json()
    expect(current.current_content).toBe('另一窗口的新稿')
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
