import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('模块分流可保存、刷新、保留分页草稿并提示配置失效', async ({ page, request }, testInfo) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '分流界面验收' } })).json()
  const model = await (await request.post(`${api}/llm/providers`, { data: {
    provider_name: '整理专用', model: 'test-routing', base_url: 'https://routing.invalid/v1', api_key: 'test-only', enabled: false,
  } })).json()
  try {
    await page.goto(`/books/${book.id}/settings`)
    await page.getByRole('tab', { name: '模块分流', exact: true }).click()
    const summary = page.getByRole('combobox', { name: '单章梗概使用的模型', exact: true })
    const state = page.getByRole('combobox', { name: '当前状态速查使用的模型', exact: true })
    await expect(summary).toBeVisible()
    await expect(page.getByRole('combobox', { name: '批量梗概使用的模型', exact: true })).toBeVisible()
    await summary.selectOption(String(model.id))
    await page.getByRole('tab', { name: '模型列表', exact: true }).click()
    await page.getByRole('tab', { name: '模块分流', exact: true }).click()
    await expect(summary).toHaveValue(String(model.id))
    await page.getByRole('button', { name: '保存单章梗概分流', exact: true }).click()
    await expect(page.getByRole('button', { name: '保存单章梗概分流', exact: true })).toBeDisabled()
    await state.selectOption(String(model.id))
    await page.getByRole('button', { name: '保存当前状态速查分流', exact: true }).click()
    await expect(page.getByRole('button', { name: '保存当前状态速查分流', exact: true })).toBeDisabled()
    await page.reload()
    await page.getByRole('tab', { name: '模块分流', exact: true }).click()
    await expect(summary).toHaveValue(String(model.id))
    await expect(state).toHaveValue(String(model.id))
    await page.screenshot({ path: testInfo.outputPath('llm-routing.png'), fullPage: true })
    // Another window updates this row; saving the old baseline must retain the draft.
    const rows = await (await request.get(`${api}/llm/routes`)).json()
    const row = rows.find((r: { module: string }) => r.module === 'current_state')
    await request.put(`${api}/llm/routes/current_state`, { data: { provider_id: null, base_revision: row.revision } })
    await state.selectOption('')
    await page.getByRole('button', { name: '保存当前状态速查分流', exact: true }).click()
    await expect(page.getByText('此模块的模型分流已在其他窗口修改', { exact: false })).toBeVisible()
    await expect(state).toHaveValue('')
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: '重新读取分流', exact: true }).click()
    await expect(page.getByRole('button', { name: '保存当前状态速查分流', exact: true })).toBeDisabled()
    await request.delete(`${api}/llm/providers/${model.id}`)
    await page.getByRole('button', { name: '重新读取分流', exact: true }).click()
    await expect(summary).toHaveValue('missing')
    await expect(page.getByText('单章梗概指定的模型已删除或未设置密钥', { exact: false })).toBeVisible()
    await summary.selectOption('')
    await page.getByRole('button', { name: '保存单章梗概分流', exact: true }).click()
    await expect(page.getByRole('button', { name: '保存单章梗概分流', exact: true })).toBeDisabled()
  } finally {
    const rows = await (await request.get(`${api}/llm/routes`)).json()
    for (const row of rows) await request.put(`${api}/llm/routes/${row.module}`, { data: { provider_id: null, base_revision: row.revision } })
    await request.delete(`${api}/llm/providers/${model.id}`)
    await request.delete(`${api}/books/${book.id}`)
  }
})
