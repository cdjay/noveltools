import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('独立速查页可生成、编辑提示词、保存历史并提供提取地址', async ({ page, request }, testInfo) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '状态速查界面验收' } })).json()
  const chapters = []
  for (let index = 1; index <= 6; index++) chapters.push(await (await request.post(`${api}/books/${book.id}/chapters`, { data: { title: `第${index}次相遇`, content: `正文内容${index}` } })).json())
  const base = `${api}/books/${book.id}/current-state`
  try {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`/books/${book.id}/current-state`)
    await expect(page.getByRole('heading', { name: '当前状态速查', exact: true })).toBeVisible()
    await expect(page.getByText('正文范围：', { exact: false })).toContainText('第 2 章、第 3 章、第 4 章、第 5 章、第 6 章')
    await page.getByRole('button', { name: '页面设置' }).click()
    const prompt = page.getByRole('textbox', { name: '当前状态速查提示词', exact: true })
    await prompt.fill('请用 Markdown 整理当前进度、人物状态与伏笔，未知信息标为待核实。')
    await page.getByRole('button', { name: '保存提示词', exact: true }).click()
    await expect(page.getByRole('button', { name: '保存提示词', exact: true })).toBeDisabled()
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    expect((await (await request.get(base + '/settings')).json()).prompt).toContain('未知信息标为待核实')
    // UI exercises a delayed generation response while HTTP-to-LLM payloads are tested server-side.
    await page.route(`**/books/${book.id}/current-state/generate`, async route => {
      const result = await request.post(base + '/versions', { data: { content: '## 当前进度\n\n第六章结束。\n\n## 人物状态\n\n| 人物 | 状态 |\n| --- | --- |\n| 沈舟 | 出发 |', base_version_id: null } })
      await route.fulfill({ json: await result.json() })
    })
    await page.getByRole('button', { name: '生成当前状态速查' }).click()
    const rendered = page.getByLabel('当前状态速查全文预览', { exact: true })
    await expect(rendered.getByRole('heading', { name: '当前进度' })).toBeVisible()
    await expect(rendered.getByRole('table')).toBeVisible()
    await expect(page.getByRole('textbox', { name: '当前状态速查全文', exact: true })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '读取 JSON' })).toHaveAttribute('href', base)
    await expect(page.getByRole('link', { name: '下载 Markdown' })).toHaveAttribute('href', base + '/export.md')
    await page.getByRole('button', { name: '原始', exact: true }).click()
    const editor = page.getByRole('textbox', { name: '当前状态速查全文', exact: true })
    await editor.fill('## 当前进度\n\n作者补充核对后的速查。')
    await expect(page.getByRole('button', { name: '生成当前状态速查' })).toBeDisabled()
    await page.getByRole('button', { name: '保存修改', exact: true }).click()
    await expect(page.getByText('当前 v2', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '查看 v1', exact: true }).click()
    await expect(page.getByLabel('历史状态速查全文', { exact: true })).toContainText('第六章结束')
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click()
    await page.getByRole('button', { name: '恢复', exact: true }).click()
    await page.getByRole('button', { name: '恢复为新版本', exact: true }).click()
    await expect(page.getByText('当前 v3', { exact: true })).toBeVisible()
    await request.post(`${api}/chapters/${chapters[5].id}/versions`, { data: { content: '最新正文已经改变' } })
    await page.reload()
    await expect(page.getByText('来源已变化，建议重新生成', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Markdown', exact: true }).click()
    await expect(rendered.getByRole('table')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('current-state.png'), fullPage: true })
    await page.getByRole('button', { name: '页面设置' }).click()
    await expect(prompt).toHaveValue('请用 Markdown 整理当前进度、人物状态与伏笔，未知信息标为待核实。')
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
