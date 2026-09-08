import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('正文工具栏选择章数、启动、停止、继续和刷新任务，保留编辑草稿', async ({ page, request }, testInfo) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '持续生成验收' } })).json()
  await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '已有章节', content: '已有正文' } })
  let job: any = null
  let starts = 0
  let summaryReady = false
  await page.route(`**/books/${book.id}/chapters?include_content=false`, async route => {
    const response = await route.fetch()
    const chapters = await response.json()
    await route.fulfill({ json: chapters.map((chapter: any) => summaryReady ? { ...chapter, summary: '工作流已更新梗概', summary_model: 'k3' } : chapter) })
  })
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
    summaryReady = true
    const chapter = await (await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '自动生成的新章', content: '模型返回的完整正文' } })).json()
    job = { ...job, status: 'running', step: 'state', message: '更新速查', saved_count: 1,
      items: [{ sequence: 1, chapter_id: chapter.id, title: chapter.title, status: 'generated' }] }
    await route.fulfill({ json: job })
  })
  try {
    await page.setViewportSize({ width: 1500, height: 1000 })
    await page.goto(`/books/${book.id}/reader`)
    const start = page.getByRole('button', { name: '开始生成正文', exact: true })
    const count = page.getByRole('combobox', { name: '持续生成章数', exact: true })
    const floating = page.getByRole('region', { name: '持续生成正文浮窗', exact: true })
    const trigger = page.getByRole('button', { name: '持续生成正文', exact: true })
    const toolbar = page.getByRole('button', { name: '切换章节列表' }).locator('..')
    const before = await toolbar.boundingBox()
    await trigger.click()
    await expect(start).toBeEnabled()
    const initial = (await floating.boundingBox())!
    expect(Math.round(initial.x + initial.width)).toBe(1480)
    expect(Math.round(initial.y + initial.height)).toBe(980)
    expect(await count.locator('option').allTextContents()).toEqual(['1 章', '3 章', '5 章', '10 章', '20 章', '30 章', '50 章', '100 章'])
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await page.getByRole('button', { name: '编辑', exact: true }).click()
    const body = page.getByRole('textbox', { name: '章节正文', exact: true })
    await body.fill('尚未保存的草稿')
    await trigger.click()
    await expect(start).toBeDisabled()
    await body.fill('已有正文')
    await trigger.click()
    await count.selectOption('3')
    await start.click()
    await expect(start).toBeDisabled()
    await expect(page.getByLabel('正文生成进度')).toContainText('补齐梗概')
    await expect(page.getByText('正文模型：gemini-test', { exact: true })).toBeVisible()
    await expect(page.getByText('当前步骤：正在补齐梗概', { exact: true })).toBeVisible()
    expect((await toolbar.boundingBox())?.height).toBe(before?.height)
    await page.getByRole('button', { name: '停止生成', exact: true }).click()
    await page.getByRole('button', { name: '继续生成', exact: true }).click()
    await expect(page.getByText('正文已保存 1/3 章', { exact: false })).toBeVisible()
    await expect(page.getByRole('region', { name: '步骤日志', exact: true })).toContainText('正在补齐梗概')
    await expect(page.getByRole('button', { name: /自动生成的新章/ })).toBeVisible()
    await expect(page.getByLabel('本章梗概内容', { exact: true })).toContainText('工作流已更新梗概')
    await expect(body).toHaveValue('已有正文')
    await body.fill('生成期间保留的草稿')
    await trigger.click()
    await page.getByRole('button', { name: '查看章节', exact: true }).click()
    // Switching uses the reader's existing draft confirmation modal.
    await expect(body).toHaveValue('生成期间保留的草稿')
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    await trigger.click()
    await page.screenshot({ path: testInfo.outputPath('writing-workflow.png'), fullPage: true })
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(floating).toBeVisible()
    expect(starts).toBe(1)
    // Drop only the test draft before verifying a reload reads the running task.
    await body.fill('已有正文')
    await page.getByRole('button', { name: '大纲', exact: true }).click()
    await expect(page.getByRole('heading', { name: '大纲', exact: true })).toBeVisible()
    await expect(floating).toBeVisible()
    await page.getByRole('button', { name: '收起生成浮窗' }).click()
    await expect(floating).toBeVisible()
    await expect(start).toBeHidden()
    await expect(floating.getByRole('progressbar')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('writing-collapsed.png'), fullPage: true })
    await page.getByRole('button', { name: '展开生成浮窗' }).click()
    await expect(start).toBeVisible()
    // Dragging stays inside the viewport, and a smaller viewport keeps controls reachable.
    const header = floating.locator('header')
    const bounds = (await header.boundingBox())!
    await page.mouse.move(bounds.x + 70, bounds.y + 20)
    await page.mouse.down()
    await page.mouse.move(50, 70)
    await page.mouse.up()
    expect((await floating.boundingBox())!.x).toBeGreaterThanOrEqual(8)
    await page.setViewportSize({ width: 600, height: 700 })
    await expect.poll(async () => {
      const rect = (await floating.boundingBox())!
      return rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= 600 && rect.y + rect.height <= 700
    }).toBe(true)
    await page.setViewportSize({ width: 1500, height: 1000 })
    await page.getByRole('button', { name: '关闭生成浮窗' }).click()
    await expect(floating).toBeHidden()
    await page.getByRole('button', { name: '正文', exact: true }).click()
    await trigger.click()
    await expect(page.getByText('正文已保存 1/3 章', { exact: false })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await trigger.click()
    await expect(page.getByText('正文已保存 1/3 章', { exact: false })).toBeVisible()
    await expect(start).toBeDisabled()
    await expect(page.getByRole('region', { name: '步骤日志', exact: true })).toContainText('正在补齐梗概')
    job = { ...job, status: 'failed', message: '模型接口返回 HTTP 502，请检查 LLM 配置。', failover_events: [{ id: 10, time: new Date().toISOString(), text: '批量梗概：切换至备用模型 backup-test，发送相同提示词和资料。' }] }
    await expect(floating.getByRole('alert')).toContainText('HTTP 502')
    await expect(page.getByRole('region', { name: '步骤日志', exact: true })).toContainText('backup-test')
    await expect(page.getByRole('button', { name: '继续生成', exact: true })).toBeEnabled()
    await expect(page.getByRole('region', { name: '步骤日志', exact: true })).toContainText('HTTP 502')
    await page.getByRole('button', { name: '收起生成浮窗' }).click()
    await expect(floating.getByText('更新速查 · 模型接口返回 HTTP 502，请检查 LLM 配置。', { exact: true })).toBeVisible()
  } finally { await request.delete(`${api}/books/${book.id}`).catch(() => undefined) }
})
