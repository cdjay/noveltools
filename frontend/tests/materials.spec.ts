import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('资料库支持写作规则、普通资料、Markdown、复制与历史恢复', async ({ page, request, context }, testInfo) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const book = await (await request.post(`${api}/books`, { data: { title: '资料库界面验收' } })).json()
  const base = `${api}/books/${book.id}/materials`
  try {
    await page.setViewportSize({ width: 1500, height: 1000 })
    await page.goto(`/books/${book.id}/materials`)
    await expect(page.getByRole('heading', { name: '书籍资料库', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '填入写作规则模板' }).click()
    const editor = page.getByRole('textbox', { name: '资料正文', exact: true })
    await expect(editor).toContainText('必须遵守的要求')
    const source = '# 本书规则\n\n## 必须遵守\n使用第三人称。\n\n| 要求 | 类型 |\n| --- | --- |\n| 少用感叹号 | 建议 |'
    await editor.fill(source)
    await page.getByRole('button', { name: 'Markdown', exact: true }).click()
    const rendered = page.getByLabel('资料正文预览', { exact: true })
    await expect(rendered.getByRole('table')).toBeVisible()
    await expect(editor).toHaveCount(0)
    await page.getByRole('button', { name: '保存资料', exact: true }).click()
    await expect(page.getByText('当前 v1', { exact: true })).toBeVisible()
    expect((await (await request.get(base + '/writing-rules')).json()).version.content).toBe(source)
    await page.getByRole('button', { name: '复制资料正文预览', exact: true }).click()
    await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/g, '\n'))).toBe(source)
    await expect(page.getByRole('link', { name: '读取资料 JSON' })).toHaveAttribute('href', base + '/writing-rules')
    await page.screenshot({ path: testInfo.outputPath('writing-rules.png'), fullPage: true })
    await page.getByRole('button', { name: '新建资料', exact: true }).click()
    await page.getByLabel('新资料标题', { exact: true }).fill('情节灵感')
    await page.getByRole('button', { name: '创建资料', exact: true }).click()
    await expect(page.getByLabel('资料标题', { exact: true })).toHaveValue('情节灵感')
    await editor.fill('## 灵感\n古桥下的约定。')
    await page.getByRole('button', { name: '保存资料', exact: true }).click()
    await expect(page.getByText('当前 v2', { exact: true })).toBeVisible()
    await page.getByLabel('资料标题', { exact: true }).fill('古桥约定')
    await editor.fill('已经修改的资料。')
    await page.getByRole('button', { name: '保存资料', exact: true }).click()
    await expect(page.getByText('当前 v3', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '查看 v2', exact: true }).click()
    await expect(page.getByLabel('历史资料全文', { exact: true })).toContainText('古桥下的约定')
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click()
    await page.getByRole('button', { name: '恢复 v2', exact: true }).click()
    await page.getByRole('button', { name: '恢复为新版本', exact: true }).click()
    await expect(page.getByText('当前 v4', { exact: true })).toBeVisible()
    await expect(page.getByLabel('资料标题', { exact: true })).toHaveValue('情节灵感')
    await expect(editor).toHaveValue('## 灵感\n古桥下的约定。')
    await page.getByRole('navigation', { name: '本书资料列表' }).getByRole('button', { name: /写作规则/ }).click()
    await expect(editor).toHaveValue(source)
    await page.reload()
    await expect(editor).toHaveValue(source)
    await page.setViewportSize({ width: 900, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  } finally { await request.delete(`${api}/books/${book.id}`) }
})

test('资料切换保护草稿，旧窗口保存不会覆盖新版本', async ({ page, request }) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '资料草稿保护' } })).json()
  const base = `${api}/books/${book.id}/materials`
  const rule = base + '/writing-rules'
  try {
    await request.post(base, { data: { title: '另一份资料' } })
    await page.goto(`/books/${book.id}/materials`)
    const editor = page.getByRole('textbox', { name: '资料正文', exact: true })
    await editor.fill('本地待保存草稿')
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('navigation', { name: '本书资料列表' }).getByRole('button', { name: /另一份资料/ }).click()
    await expect(editor).toHaveValue('本地待保存草稿')
    await request.post(rule + '/versions', { data: { title: '写作规则', content: '其他窗口的新稿', base_version_id: null } })
    await page.getByRole('button', { name: '保存资料', exact: true }).click()
    await expect(page.getByText('这份资料已在其他窗口更新。', { exact: false })).toBeVisible()
    await expect(editor).toHaveValue('本地待保存草稿')
    expect((await (await request.get(rule)).json()).version.content).toBe('其他窗口的新稿')
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: '重新读取', exact: true }).click()
    await expect(editor).toHaveValue('其他窗口的新稿')
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
