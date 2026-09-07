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
})
