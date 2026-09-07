import { test, expect } from '@playwright/test'
const api = 'http://127.0.0.1:19518/api'

test('书籍备份可从书架预览并恢复为副本', async ({ page, request }) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '备份恢复验收' } })).json()
  let restoredId: number | undefined
  try {
    await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '初见', content: '正文不会被覆盖。' } })
    await page.goto(`/books/${book.id}/reader`)
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('link', { name: '备份书籍', exact: true }).click()
    const download = await downloadPromise
    const path = await download.path()
    await page.getByRole('link', { name: '书架', exact: true }).click()
    await page.getByLabel('书籍备份文件').setInputFiles(path!)
    await expect(page.getByRole('dialog')).toContainText('1 章 · 1 份正文版本')
    await page.getByRole('button', { name: '恢复为新书籍' }).click()
    await expect(page.getByRole('heading', { name: '备份恢复验收（恢复副本）', exact: true })).toBeVisible()
    const books = await (await request.get(`${api}/books`)).json()
    restoredId = books.find((b: { title: string }) => b.title === '备份恢复验收（恢复副本）').id
    expect((await (await request.get(`${api}/books/${restoredId}/chapters`)).json())[0].current_content).toBe('正文不会被覆盖。')
  } finally {
    await request.delete(`${api}/books/${book.id}`)
    if (restoredId) await request.delete(`${api}/books/${restoredId}`)
  }
})
