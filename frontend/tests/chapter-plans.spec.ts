import { expect, test } from '@playwright/test'

const api = 'http://127.0.0.1:19518/api'

test('章纲独立编辑、历史与关联，移动端无横向溢出', async ({ page, request }, testInfo) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '章纲页面验收' } })).json()
  try {
    await page.goto(`/books/${book.id}/chapter-plans`)
    await page.getByRole('button', { name: '新建章纲', exact: true }).click()
    await page.getByRole('textbox', { name: '拟定章节名', exact: true }).fill('死字号')
    await page.getByRole('textbox', { name: '章纲内容', exact: true }).fill('## 目标\n陈渡完成首次引气。\n\n## 冲突\n守卫即将检查。')
    await page.getByRole('button', { name: '保存章纲', exact: true }).click()
    await expect(page.getByText('章纲 · v1', { exact: true })).toBeVisible()
    expect((await (await request.get(`${api}/books/${book.id}`)).json()).chapter_count).toBe(0)
    await page.getByLabel('章纲状态', { exact: true }).selectOption('confirmed')
    await page.getByRole('button', { name: '保存章纲', exact: true }).click()
    await expect(page.getByText('章纲 · v2', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '查看 v1', exact: true }).click()
    await expect(page.getByLabel('历史章纲', { exact: true })).toContainText('陈渡完成首次引气')
    await page.getByRole('button', { name: '恢复为新草稿版本', exact: true }).click()
    await expect(page.getByText('章纲 · v3', { exact: true })).toBeVisible()
    await expect(page.getByLabel('章纲状态', { exact: true })).toHaveValue('draft')
    // A chapter can be linked later, by stable identity.
    const c = await (await request.post(`${api}/books/${book.id}/chapters`, { data: { title: '已写章节', content: '已保存的正文。' } })).json()
    await page.reload()
    await page.getByLabel('关联正文', { exact: true }).selectOption(String(c.id))
    await page.getByRole('button', { name: '保存关联', exact: true }).click()
    await expect(page.getByRole('link', { name: '打开第1章正文' })).toBeVisible()
    await page.getByRole('button', { name: '已核对当前正文', exact: true }).click()
    await expect(page.getByText('已核对当前版本', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Markdown', exact: true }).click()
    await expect(page.getByLabel('章纲内容预览', { exact: true }).getByRole('heading', { name: '目标' })).toBeVisible()
    await page.getByRole('heading', { name: '章纲', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath('chapter-plans-desktop.png'), fullPage: true })
    await page.setViewportSize({ width: 800, height: 900 })
    await page.getByRole('heading', { name: '章纲', exact: true }).scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('chapter-plans-narrow.png'), fullPage: true })
    await page.getByRole('link', { name: '打开第1章正文' }).click()
    await expect(page.getByText('本章章纲', { exact: true })).toBeVisible()
  } finally { await request.delete(`${api}/books/${book.id}`) }
})

test('章纲排序与编辑冲突不丢草稿', async ({ page, request }) => {
  const book = await (await request.post(`${api}/books`, { data: { title: '章纲并发验收' } })).json()
  try {
    const path = `${api}/books/${book.id}/chapter-plans`
    const first = await (await request.post(path, { data: { title: '第一', content: '原版' } })).json()
    const second = await (await request.post(path, { data: { title: '第二', content: '次章' } })).json()
    await page.goto(`/books/${book.id}/chapter-plans`)
    await page.getByRole('button', { name: '下移第一', exact: true }).click()
    await expect.poll(async () => (await (await request.get(path)).json()).plans[0].id).toBe(second.id)
    await page.getByRole('textbox', { name: '章纲内容', exact: true }).fill('本地未保存草稿')
    await request.post(`${path}/${first.id}/versions`, { data: { title: '第一', content: '其他窗口保存', base_version_id: first.version.id } })
    await page.getByRole('button', { name: '保存章纲', exact: true }).click()
    await expect(page.getByText('章纲已在其他窗口更新，请保留草稿并重新读取。', { exact: true })).toBeVisible()
    await expect(page.getByRole('textbox', { name: '章纲内容', exact: true })).toHaveValue('本地未保存草稿')
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name: '大纲', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/books/${book.id}/chapter-plans$`))
  } finally { await request.delete(`${api}/books/${book.id}`) }
})
