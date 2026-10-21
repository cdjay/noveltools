import {expect,test} from '@playwright/test'
const api='http://127.0.0.1:19518/api'

test('章纲续写选择数量、运行中关闭窗口，完成后直接更新章纲列表',async({page,request},testInfo)=>{
  const book=await(await request.post(`${api}/books`,{data:{title:'章纲续写验收'}})).json()
  let started:any=null,job:any=null
  await page.route('**/api/canvas-writing/workflows',route=>route.fulfill({json:[{id:'fixture-loop',name:'测试章纲循环'}]}))
  await page.route(`**/api/books/${book.id}/plan-writing/latest`,route=>route.fulfill({json:job}))
  await page.route(`**/api/books/${book.id}/plan-writing/settings`,route=>route.fulfill({json:{workflow_id:'fixture-loop'}}))
  await page.route(`**/api/books/${book.id}/plan-writing`,async route=>{
    started=route.request().postDataJSON()
    job={id:'fixture-job',book_id:book.id,status:'running',message:'当前章第 1 轮生成 / 审核',target_count:started.chapter_count,completed_count:0,workflow_id:'fixture-loop',run_id:'test-run',round:1,items:[],can_resume:false,has_output:false}
    await route.fulfill({json:job})
  })
  await page.route('**/api/plan-writing/jobs/fixture-job',route=>route.fulfill({json:job}))
  try {
    await page.goto(`/books/${book.id}/chapter-plans`)
    await page.getByRole('button',{name:'章纲续写',exact:true}).click()
    await expect(page.getByLabel('章纲生成数量',{exact:true})).toHaveValue('1')
    await page.getByLabel('章纲生成数量',{exact:true}).fill('2')
    await page.getByLabel('章纲生成方式').selectOption('regenerate')
    await page.getByLabel('起始章纲位置',{exact:true}).fill('1')
    await page.getByLabel('章纲续写要求').fill('保持主角主动推进')
    await page.getByRole('button',{name:'开始重生成',exact:true}).click()
    await expect.poll(()=>started?.chapter_count).toBe(2)
    expect(started.start_position).toBe(1)
    expect(started.requirements).toBe('保持主角主动推进')
    await expect(page.getByRole('button',{name:'停止续写'})).toBeVisible()
    await page.screenshot({path:testInfo.outputPath('plan-continuation-desktop.png'),fullPage:true})
    await page.getByRole('button',{name:'关闭',exact:true}).last().click()
    const a=await(await request.post(`${api}/books/${book.id}/chapter-plans`,{data:{title:'找到线索',content:'主角主动调查获得线索'}})).json()
    job={...job,completed_count:1,items:[{plan_id:a.id,position:1,title:'找到线索',run_id:'first'}]}
    await expect(page.getByRole('button',{name:'章纲 1 找到线索',exact:true})).toBeVisible({timeout:10000})
    const b=await(await request.post(`${api}/books/${book.id}/chapter-plans`,{data:{title:'继续追查',content:'利用上一章线索推进'}})).json()
    job={...job,status:'completed',message:'已完成 2/2 份章纲',completed_count:2,items:[...job.items,{plan_id:b.id,position:2,title:'继续追查',run_id:'second'}]}
    await expect(page.getByRole('button',{name:'章纲 2 继续追查',exact:true})).toBeVisible({timeout:10000})
    await page.setViewportSize({width:800,height:900})
    await page.getByRole('button',{name:'章纲续写',exact:true}).click()
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.screenshot({path:testInfo.outputPath('plan-continuation-narrow.png'),fullPage:true})
  } finally {await request.delete(`${api}/books/${book.id}`)}
})
