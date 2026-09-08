import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('进入模型设置选中默认配置，复制后只改模型即可保存', async ({ page, request }, testInfo) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '复制模型验收' } })).json()
  const source = await (await request.post(`${api}/llm/providers`, { data: {
    provider_name: 'sub2api 默认', base_url: 'https://copy-ui.invalid/v1', api_key: 'test-only-secret', model: 'default-model', enabled: true,
  } })).json()
  const newer = await (await request.post(`${api}/llm/providers`, { data: {
    provider_name: '较新配置', base_url: 'https://copy-ui.invalid/v1', model: 'newer-model', enabled: false,
  } })).json()
  let copiedId: number | undefined
  try {
    await page.goto(`/books/${book.id}/settings`)
    await expect(page.getByLabel('名称', { exact: true })).toHaveValue('sub2api 默认')
    await expect(page.getByLabel('模型名', { exact: true })).toHaveValue('default-model')
    await expect(page.getByRole('checkbox', { name: '设为默认模型' })).toBeChecked()
    await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
    const copy = page.getByRole('button', { name: '复制配置', exact: true })
    await page.getByLabel('模型名', { exact: true }).fill('unsaved-model')
    await expect(copy).toBeDisabled()
    await page.getByLabel('模型名', { exact: true }).fill('default-model')
    const responsePromise = page.waitForResponse(r => r.url().endsWith(`/providers/${source.id}/copy`))
    await copy.click()
    const copied = await (await responsePromise).json()
    copiedId = copied.id
    expect(copied.has_api_key).toBe(true)
    expect(JSON.stringify(copied)).not.toContain('test-only-secret')
    await expect(page.getByLabel('名称', { exact: true })).toHaveValue('sub2api 默认 副本')
    await expect(page.getByLabel('Base URL', { exact: true })).toHaveValue('https://copy-ui.invalid/v1')
    await expect(page.getByRole('checkbox', { name: '设为默认模型' })).not.toBeChecked()
    await page.getByLabel('模型名', { exact: true }).fill('gemini-model')
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await expect(copy).toBeEnabled()
    const providers = await (await request.get(`${api}/llm/providers`)).json()
    expect(providers.find((p: { id: number }) => p.id === copiedId)).toMatchObject({ model: 'gemini-model', has_api_key: true, enabled: false })
    expect(providers.find((p: { id: number }) => p.id === source.id)).toMatchObject({ model: 'default-model', enabled: true })
    await page.screenshot({ path: testInfo.outputPath('copied-model.png'), fullPage: true })
    await page.reload()
    await expect(page.getByLabel('模型名', { exact: true })).toHaveValue('default-model')
    await page.getByRole('button', { name: '新建', exact: true }).click()
    await expect(page.getByLabel('模型名', { exact: true })).toHaveValue('')
    await expect(page.getByLabel('Base URL', { exact: true })).toHaveValue('')
  } finally {
    if (copiedId) await request.delete(`${api}/llm/providers/${copiedId}`)
    await request.delete(`${api}/llm/providers/${source.id}`)
    await request.delete(`${api}/llm/providers/${newer.id}`)
    await request.delete(`${api}/books/${book.id}`)
  }
})
