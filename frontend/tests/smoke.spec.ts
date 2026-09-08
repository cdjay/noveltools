import { test, expect, type APIRequestContext } from '@playwright/test'

const TEST_BOOK_TITLE = 'Playwright 测试书'
const TEST_BACKEND_ORIGIN = 'http://127.0.0.1:19518'

async function removeTestBooks(request: APIRequestContext) {
  const response = await request.get(`${TEST_BACKEND_ORIGIN}/api/books`)
  expect(response.ok()).toBeTruthy()
  const books = await response.json() as Array<{ id: number, title: string }>
  for (const book of books.filter(item => item.title === TEST_BOOK_TITLE)) {
    const deletion = await request.delete(`${TEST_BACKEND_ORIGIN}/api/books/${book.id}`)
    expect(deletion.ok()).toBeTruthy()
  }
}

test.beforeEach(async ({ request }) => {
  await removeTestBooks(request)
})

test.afterEach(async ({ request }) => {
  await removeTestBooks(request)
})

test('书架可以创建书籍并进入阅读器', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('我的书架')).toBeVisible()
  await page.getByRole('button', { name: '新建书籍' }).click()
  await page.getByRole('textbox', { name: '书名', exact: true }).fill(TEST_BOOK_TITLE)
  const createResponsePromise = page.waitForResponse(response => (
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/books'
  ))
  await page.getByRole('button', { name: '创建书籍' }).click()
  const createResponse = await createResponsePromise
  expect(new URL(createResponse.url()).origin).toBe(TEST_BACKEND_ORIGIN)
  await expect(page.getByRole('heading', { name: TEST_BOOK_TITLE }).first()).toBeVisible()
  await page.getByRole('button', { name: '进入工作区' }).first().click()
  await expect(page.getByText('这本书还没有章节')).toBeVisible()
})

test('LLM 设置只提供普通 OpenAI Compatible 配置', async ({ page, request }) => {
  const created = await request.post(`${TEST_BACKEND_ORIGIN}/api/books`, { data: { title: TEST_BOOK_TITLE } })
  expect(created.ok()).toBeTruthy()
  const book = await created.json() as { id: number }

  await page.goto(`/books/${book.id}/settings`)

  await expect(page.getByRole('heading', { name: 'LLM 配置' })).toBeVisible()
  await expect(page.getByText('WebAI-to-API')).toHaveCount(0)
  await expect(page.getByText('星月')).toHaveCount(0)
  await expect(page.getByText('API Key', { exact: true })).toBeVisible()
  await expect(page.getByLabel('生成等待（分钟）', { exact: true })).toHaveValue('30')
})

test('LLM 未保存配置不能误测旧配置，停用状态独立提示', async ({ page, request }) => {
  const book = await (await request.post(`${TEST_BACKEND_ORIGIN}/api/books`, { data: { title: TEST_BOOK_TITLE } })).json()
  let provider = { id: 900, provider_name: '测试专用模型', provider_type: 'openai_compatible',
    base_url: 'https://model.invalid/v1', model: 'saved-model', temperature: 0.2,
    max_tokens: 8192, timeout: 60, enabled: false, has_api_key: true }
  await page.route('**/api/llm/providers', route => route.fulfill({ json: [provider] }))
  await page.route('**/api/llm/providers/900', route => {
    const payload = route.request().postDataJSON()
    expect(payload.timeout).toBe(3600)
    provider = { ...provider, ...payload }
    return route.fulfill({ json: provider })
  })
  let calls = 0
  await page.route('**/api/llm/providers/900/test', route => {
    calls++; return route.fulfill({ json: { ok: true, message: '连接成功' } })
  })
  await page.goto(`/books/${book.id}/settings`)
  await page.getByRole('button', { name: /测试专用模型/ }).click()
  await expect(page.getByText('此配置当前停用。', { exact: false })).toBeVisible()
  const button = page.getByRole('button', { name: '测试连接', exact: true })
  await button.click()
  expect(calls).toBe(1)
  await page.getByLabel('模型名', { exact: true }).fill('unsaved-model')
  await expect(button).toBeDisabled()
  await expect(page.getByText('配置有未保存修改，请先保存，再测试连接或读取模型。')).toBeVisible()
  await page.getByLabel('模型名', { exact: true }).fill('saved-model')
  await expect(button).toBeEnabled()
  await page.getByLabel('生成等待（分钟）', { exact: true }).fill('60')
  await page.getByRole('button', { name: '保存配置', exact: true }).click()
  await expect(button).toBeEnabled()
  expect(provider.timeout).toBe(3600)
  await expect(page.getByLabel('生成等待（分钟）', { exact: true })).toHaveValue('60')
})
