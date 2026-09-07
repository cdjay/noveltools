import { test, expect } from '@playwright/test'
const api = 'http://127.0.0.1:19518/api'

test('整本正文预览拆章并调整章节顺序', async ({ page, request }) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '导入排序验收' } })).json()
  try {
    await page.goto(`/books/${book.id}/reader`)
    await page.getByLabel('导入正文文件').setInputFiles({ name: '全书.txt', mimeType: 'text/plain', buffer: Buffer.from('第一章 开端\n内容一\n第二章 重逢\n内容二') })
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('button', { name: '确认导入 2 章' })).toBeEnabled()
    expect(await (await request.get(`${api}/books/${book.id}/chapters`)).json()).toHaveLength(0)
    await page.getByRole('button', { name: '确认导入 2 章' }).click()
    await page.getByRole('button', { name: '调整章节顺序' }).click()
    await page.getByRole('button', { name: '上移 重逢' }).click()
    await page.getByRole('button', { name: '保存顺序' }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    const chapters = await (await request.get(`${api}/books/${book.id}/chapters`)).json()
    expect(chapters.map((c: { title: string }) => c.title)).toEqual(['重逢', '开端'])
    expect(chapters[0].current_content).toBe('内容二')
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
