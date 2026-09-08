import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('正文工具栏选择章数、启动、停止、继续和刷新任务，保留编辑草稿', async ({ page, request }, testInfo) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '持续生成验收' } })).json()
  await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '已有章节', content: '已有正文' } })
  let job: any = null
  let starts = 0
  await page.route(`**/books/${book.id}/writing-jobs/latest`, route => route.fulfill({ json: job }))
  await page.route(`**/books/${book.id}/writing-jobs`, async route => {
    expect(route.request().postDataJSON()).toEqual({ chapter_count: 3 })
    starts++
    job = { id: 501, status: 'running', step: 'summaries', message: '正在补齐梗概', target_count: 3, completed_count: 0, saved_count: 0, model: 'gemini-test', items: [] }
    await route.fulfill({ json: job })
  })
  await page.route('**/writing-jobs/501/cancel', route => {
    job = { ...job, status: 'cancelled', message: '已停止' }
    return route.fulfill({ json: job })
  })
  await page.route('**/writing-jobs/501/resume', async route => {
    const chapter = await (await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '自动生成的新章', content: '模型返回的完整正文' } })).json()
    job = { ...job, status: 'running', step: 'state', message: '更新速查', saved_count: 1,
      items: [{ sequence: 1, chapter_id: chapter.id, title: chapter.title, status: 'generated' }] }
    await route.fulfill({ json: job })
  })
  try {
    await page.goto(`/books/${book.id}/reader`)
    const start = page.getByRole('button', { name: '开始生成正文', exact: true })
    const count = page.getByRole('combobox', { name: '持续生成章数', exact: true })
    await expect(start).toBeEnabled()
    expect(await count.locator('option').allTextContents()).toEqual(['1 章', '3 章', '5 章', '10 章', '20 章', '30 章', '50 章', '100 章'])
    await page.getByRole('button', { name: '编辑', exact: true }).click()
    const body = page.getByRole('textbox', { name: '章节正文', exact: true })
    await body.fill('尚未保存的草稿')
    await expect(start).toBeDisabled()
    await body.fill('已有正文')
    await count.selectOption('3')
    await start.click()
    await expect(start).toBeDisabled()
    await expect(page.getByLabel('正文生成进度').locator('summary')).toContainText('补齐梗概')
    await page.getByRole('button', { name: '停止生成', exact: true }).click()
    await page.getByRole('button', { name: '继续生成', exact: true }).click()
    await expect(page.getByText('正文已保存 1/3 章', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: /自动生成的新章/ })).toBeVisible()
    await body.fill('生成期间保留的草稿')
    await page.getByLabel('正文生成进度').locator('summary').click()
    await page.getByRole('button', { name: '查看章节', exact: true }).click()
    // Switching uses the reader's existing draft confirmation modal.
    await expect(body).toHaveValue('生成期间保留的草稿')
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    await page.screenshot({ path: testInfo.outputPath('writing-workflow.png'), fullPage: true })
    expect(starts).toBe(1)
    // Drop only the test draft before verifying a reload reads the running task.
    await body.fill('已有正文')
    await page.reload()
    await expect(page.getByText('正文已保存 1/3 章', { exact: false })).toBeVisible()
    await expect(start).toBeDisabled()
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
