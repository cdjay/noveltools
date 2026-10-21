import {expect, test} from '@playwright/test'
import {splitOpinions} from '../src/lib/reviewOpinions'
const api = 'http://127.0.0.1:19518/api'

test('主编意见拆分保留全文，支持MQK和Markdown标题', () => {
  const text = '【主编意见】\n说明\n\n## 【建议修改】\n\n**M1**\n确定问题：改字\n\n### M2｜表达\n可选建议\n\n## 【待作者决定】\n### Q1\n待定\n\n## 【重要保护项】\n### K1｜保护\n保留原文\n'
  const parts = splitOpinions(text)
  expect(parts.map(x => x.text).join('')).toBe(text)
  expect(parts.filter(x => x.kind !== 'intro').map(x => x.key)).toEqual(['M1', 'M2', 'Q1', 'K1'])
})

test('正文布局、统一确认、编辑主编意见并修订；不会自动再审核', async ({page, request}, info) => {
  await page.setViewportSize({width: 1920, height: 1080})
  const book = await (await request.post(`${api}/books`, {data: {title: '正文交互隔离验收'}})).json()
  try {
    const chapters = []
    for (let i = 1; i <= 8; i++) chapters.push(await (await request.post(`${api}/books/${book.id}/chapters`, {data: {
      title: ['山门', '死字号', '旧友', '山道', '灯火', '来客', '转机', '归途'][i-1],
      content: ('　　石壁传来轻响，他放下铁铲，仔细看向脚下。\n\n　　这一章的内容用于隔离界面验收。\n\n').repeat(8)}})).json())
    const chapter = chapters[1]
    const plan = await (await request.post(`${api}/books/${book.id}/chapter-plans`, {data: {title:'本章计划', content:'完成本章事件，保留下一章的悬念。'}})).json()
    await request.post(`${api}/books/${book.id}/chapter-plans/${plan.id}/binding`, {data: {base_version_id: plan.version.id, previous_chapter_id: null, chapter_id: chapter.id}})
    let version = chapter.current_version_id
    for (let i = 0; i < 5; i++) {
      const saved = await (await request.post(`${api}/chapters/${chapter.id}/versions`, {data: {base_version_id: version, content: chapter.current_content, title: chapter.title, note:'阅读器手动保存'}})).json()
      version = saved.id
    }
    chapter.current_version_id = version
    let reviewCount = 0, reviseCount = 0
    const opinions = '【主编意见】\n已核对本章。\n\n### M1\n- **修改目标：**使首章的亲历观察、经验推断和已确认结论层次清楚。\n定性：确定问题\n优先级：高\n\n### M2\n定性：可选建议\n可适当压缩。\n\n### Q1\n等待作者裁定。\n\n### K1\n保留主角的主动行动。'
    let job: any = null
    await page.route(`**/chapters/${chapter.id}/review/latest`, r => r.fulfill({json:job}))
    await page.route(`**/chapters/${chapter.id}/review`, async r => {
      reviewCount++
      expect(r.request().postDataJSON().base_version_id).toBe(version)
      job = {id:'review-fixture',kind:'review',book_id:book.id,chapter_id:chapter.id,base_version_id:version,status:'running',message:'审核本章中',opinions:'',saved_version_id:null}
      await r.fulfill({json:job})
      job = {...job,status:'completed',message:'审核完成，可编辑意见',opinions}
    })
    await page.route('**/chapter-review/jobs/review-fixture', r => r.fulfill({json:job}))
    await page.route(`**/chapters/${chapter.id}/revision`, async r => {
      reviseCount++
      const data = r.request().postDataJSON()
      expect(data.review_job_id).toBe('review-fixture')
      expect(data.opinions).toContain('作者修改后的意见')
      expect(data.opinions).toContain('K1')
      expect(data.decisions).toBe('')
      job = {...job,id:'revision-fixture',kind:'revision',status:'running',message:'正在修订',opinions:data.opinions}
      await r.fulfill({json:job})
      const saved = await (await request.post(`${api}/chapters/${chapter.id}/versions`, {data: {base_version_id:version, content:'　　修订后的正文已回到当前章节。',title:chapter.title,source:'review_revision'}})).json()
      version = saved.id
      job = {...job,status:'completed',message:'修订已保存，本轮结束',saved_version_id:version}
    })
    await page.route('**/chapter-review/jobs/revision-fixture', r => r.fulfill({json:job}))
    await page.goto(`/books/${book.id}/reader?chapter=${chapter.id}`)
    await expect(page.locator('article h1')).toHaveText('死字号')
    const rows = page.locator('.chapter-row')
    const compactHeight = await rows.first().evaluate(el => el.getBoundingClientRect().height)
    expect(compactHeight).toBeLessThan(60)
    expect(await rows.nth(1).evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(compactHeight)
    expect(await page.locator('.reader-context').evaluate(el => el.getBoundingClientRect().width)).toBeGreaterThan(380)
    expect(await page.locator('.reader-manuscript').evaluate(el => el.getBoundingClientRect().width)).toBeGreaterThan(700)
    expect(await page.locator('.reader-chapters').evaluate(el => getComputedStyle(el).backgroundColor)).toBe(await page.locator('.reader-workspace').evaluate(el => getComputedStyle(el).backgroundColor))
    await page.getByRole('button', {name:'确认正文', exact:true}).click()
    await expect(page.getByText('正式稿 · 已确认')).toBeVisible()
    const plans = await (await request.get(`${api}/books/${book.id}/chapter-plans`)).json()
    expect(plans.plans[0].needs_review).toBe(false)
    await page.screenshot({path:info.outputPath('reader-wide.png'),fullPage:true})
    await page.getByRole('button', {name:'审核', exact:true}).click()
    await expect(page.getByLabel('审核条目 M1', {exact:true}).getByRole('heading', {name:'M1', exact:true})).toBeVisible()
    await expect(page.getByLabel('审核条目 M1', {exact:true}).locator('strong')).toHaveText('修改目标：')
    await expect(page.getByRole('textbox', {name:'审核条目 M1', exact:true})).toHaveCount(0)
    await page.getByRole('button', {name:'编辑原文', exact:true}).click()
    await expect(page.getByRole('textbox', {name:'审核条目 M1', exact:true})).toBeVisible()
    await page.getByRole('textbox', {name:'审核条目 M1', exact:true}).fill('### M1\n确定问题：作者修改后的意见\n\n')
    await page.getByRole('button', {name:'返回排版阅读', exact:true}).click()
    await expect(page.getByLabel('审核条目 M1', {exact:true})).toContainText('作者修改后的意见')
    await page.screenshot({path:info.outputPath('review-opinions.png'),fullPage:true})
    await page.getByRole('button', {name:'修订', exact:true}).click()
    await expect(page.getByText('修订已保存，本轮结束', {exact:true})).toBeVisible()
    await page.getByRole('dialog').getByRole('button', {name:'关闭', exact:true}).last().click()
    await expect(page.getByLabel('正文内容', {exact:true})).toContainText('修订后的正文已回到当前章节')
    expect(reviewCount).toBe(1); expect(reviseCount).toBe(1)
    await page.setViewportSize({width:1280,height:900})
    await expect(page.getByRole('button', {name:'审核',exact:true})).toBeVisible()
    await expect(page.locator('.reader-context')).not.toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('button', {name:'切换梗概与历史'}).click()
    await expect(page.getByRole('button', {name:'确认正文',exact:true})).toBeVisible()
  } finally { await request.delete(`${api}/books/${book.id}`) }
})

test('生成前置错误在正文页显示且停止等待', async ({page,request}) => {
  const b = await (await request.post(`${api}/books`, {data:{title:'配置错误验收'}})).json()
  try {
    const c = await (await request.post(`${api}/books/${b.id}/chapters`, {data:{title:'空章',content:''}})).json()
    await page.route(`**/chapters/${c.id}/workflow-writing`, r => r.fulfill({status:409,json:{detail:'请在书籍提取勾选“仅从正文页传入”并保存。任务尚未启动。'}}))
    await page.goto(`/books/${b.id}/reader?chapter=${c.id}`)
    await page.getByRole('button',{name:'工作流生成正文',exact:true}).click()
    await expect(page.getByRole('alert')).toContainText('仅从正文页传入')
    await expect(page.getByRole('button',{name:'工作流生成正文',exact:true})).toBeEnabled()
    await expect(page.getByRole('button',{name:'关闭工作流进度'})).toBeVisible()
  } finally { await request.delete(`${api}/books/${b.id}`) }
})


test('长篇审核意见的修订提交错误始终显示在操作按钮旁', async ({page, request}, info) => {
  const b = await (await request.post(`${api}/books`, {data:{title:'修订错误提示验收'}})).json()
  try {
    const c = await (await request.post(`${api}/books/${b.id}/chapters`, {data:{title:'待修订章节',content:'本章已保存正文。'}})).json()
    const job = {id:'completed-review',kind:'review',book_id:b.id,chapter_id:c.id,base_version_id:c.current_version_id,
      status:'completed',message:'审核完成，可编辑主编意见后点击修订。',saved_version_id:null,
      opinions:Array.from({length:12},(_,i)=>`### M${i+1}\n确定问题：${'这是需要核对的长篇审稿意见。'.repeat(20)}\n\n`).join('')}
    await page.route(`**/chapters/${c.id}/review/latest`,r=>r.fulfill({json:job}))
    let submitted=0
    await page.route(`**/chapters/${c.id}/revision`,r=>{submitted++;return r.fulfill({status:409,json:{detail:'正文合成节点未启用，任务尚未启动。'}})})
    await page.goto(`/books/${b.id}/reader?chapter=${c.id}`)
    await page.getByRole('button',{name:'审核意见',exact:true}).click()
    await page.getByLabel('审核条目 M12',{exact:true}).scrollIntoViewIfNeeded()
    await page.getByRole('button',{name:'修订',exact:true}).click()
    const message=page.getByLabel('审核修订操作提示', {exact:true})
    await expect(message).toContainText('正文合成节点未启用')
    await expect(message).toBeInViewport()
    await expect(page.getByRole('button',{name:'修订',exact:true})).toBeEnabled()
    expect(submitted).toBe(1)
    await page.screenshot({path:info.outputPath('revision-error-footer.png'),fullPage:true})
  } finally {await request.delete(`${api}/books/${b.id}`)}
})
