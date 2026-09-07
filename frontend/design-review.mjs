/* 一次性视觉巡检：使用用户已启动的 8000 后端与 5173 前端 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const outDir = 'D:/CP/NOVELTOOLS/design-review'
mkdirSync(outDir, { recursive: true })

const base = 'http://localhost:5173'
const booksRes = await fetch('http://localhost:8000/api/books')
const books = await booksRes.json()
const bookId = books[0]?.id
console.log('books:', books.map(b => `${b.id}:${b.title}`).join(', ') || '(none)')

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const shots = [
  ['shelf', '/'],
  ...(bookId ? [
    ['reader', `/books/${bookId}/reader`],
    ['characters', `/books/${bookId}/characters`],
    ['knowledge', `/books/${bookId}/knowledge`],
    ['outline', `/books/${bookId}/outline`],
    ['timeline', `/books/${bookId}/timeline`],
    ['graphify-review', `/books/${bookId}/graphify`],
    ['analysis', `/books/${bookId}/analysis`],
    ['settings', `/books/${bookId}/settings`],
  ] : []),
]

for (const [name, path] of shots) {
  await page.goto(`${base}${path}`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  await page.screenshot({ path: `${outDir}/${name}.png` })
  console.log('shot', name)
}

if (bookId) {
  await page.goto(`${base}/books/${bookId}/graphify`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1000)
  await page.getByRole('tab', { name: '关系图' }).click()
  await page.waitForTimeout(900)
  await page.screenshot({ path: `${outDir}/graphify-graph.png` })
  await page.getByRole('tab', { name: '故事状态' }).click()
  await page.waitForTimeout(700)
  await page.screenshot({ path: `${outDir}/graphify-status.png` })
  console.log('shot graphify tabs')
  await page.goto(`${base}/books/${bookId}/reader`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: '编辑' }).click()
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${outDir}/reader-edit.png` })
  console.log('shot reader-edit')
  await page.setViewportSize({ width: 1100, height: 800 })
  await page.goto(`${base}/books/${bookId}/reader`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1000)
  await page.screenshot({ path: `${outDir}/reader-narrow.png` })
  await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' })
  await page.setViewportSize({ width: 900, height: 800 })
  await page.waitForTimeout(700)
  await page.screenshot({ path: `${outDir}/shelf-narrow.png` })
  console.log('shot narrow')
}

await browser.close()
console.log('done')
