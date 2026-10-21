import {expect,test} from '@playwright/test'
const api='http://127.0.0.1:19518/api'
test('章纲显式创建空章、冲突提示、工作流回填正文并可跨页查看进度',async({page,request},testInfo)=>{
 const book=await(await request.post(`${api}/books`,{data:{title:'画布联动验收'}})).json()
 try{
  const plan=await(await request.post(`${api}/books/${book.id}/chapter-plans`,{data:{title:'死字号',content:'首次引气',status:'confirmed'}})).json()
  await page.goto(`/books/${book.id}/chapter-plans`)
  await page.getByRole('button',{name:'章纲 1 死字号',exact:true}).click()
  await page.getByRole('button',{name:'生成正文',exact:true}).click()
  await expect(page).toHaveURL(/reader\?chapter=/)
  const chapter=(await(await request.get(`${api}/books/${book.id}/chapters`)).json())[0]
  expect(chapter.title).toBe('死字号');expect(chapter.current_content).toBe('')
  await page.route('**/api/canvas-writing/workflows',r=>r.fulfill({json:[{id:'fixture-flow',name:'自建正文流程'}]}))
  await page.route('**/api/canvas-writing/workflows/*/outputs',r=>r.fulfill({json:[{node_id:'chosen-output',label:'最终排版',port:'text'}]}))
  let selected={workflow_id:'fixture-flow',output_node_id:'',output_port:'text',revision:0}
  await page.route(`**/books/${book.id}/workflow-writing/settings`,async r=>{
   if(r.request().method()==='PUT'){
    const data=r.request().postDataJSON()
    expect(data.output_node_id).toBe('chosen-output')
    expect(data.workflow_id).toBe('fixture-flow')
    selected={...selected,...data,revision:1}
   }
   await r.fulfill({json:selected})
  })
  await page.getByRole('button',{name:'工作流设置',exact:true}).click()
  await expect(page.getByRole('combobox',{name:'正文工作流选择'})).toHaveValue('fixture-flow')
  await page.getByRole('combobox',{name:'正文输出选择'}).selectOption({label:'最终排版 · text'})
  await page.getByRole('button',{name:'保存工作流设置'}).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('button',{name:'工作流设置',exact:true}).click()
  await expect(page.getByRole('combobox',{name:'正文输出选择'})).toHaveValue(JSON.stringify(['chosen-output','text']))
  await page.getByRole('button',{name:'保存工作流设置'}).click()
  const button=page.getByRole('button',{name:'工作流生成正文',exact:true})
  await expect(button).toBeVisible()
  let job={id:'fixture-job',book_id:book.id,chapter_id:chapter.id,status:'running',message:'画布正在生成正文',workflow_id:'fixture',run_id:'run',saved_version_id:null as number|null,has_output:false,
   logs:[{id:1,time:'2026-09-10T08:00:00Z',text:'正在补齐前文梗概'},{id:2,time:'2026-09-10T08:01:00Z',text:'正文模型 · 运行中'}]}
  await page.route(`**/chapters/${chapter.id}/workflow-writing`,async route=>{expect(route.request().postDataJSON().base_version_id).toBe(chapter.current_version_id);await route.fulfill({json:job})})
  await page.route('**/workflow-writing/jobs/fixture-job',route=>route.fulfill({json:job}))
  await button.click()
  const floating=page.getByRole('complementary',{name:'画布正文工作流'})
  await expect(floating).toContainText('画布正在生成正文')
  const logs=floating.getByRole('log',{name:'工作流运行日志'})
  await expect(logs).toContainText('正在补齐前文梗概')
  await expect(logs).toContainText('正文模型 · 运行中')
  await page.getByRole('button',{name:'折叠工作流进度'}).click()
  await expect(page.getByRole('progressbar',{name:'画布执行阶段'})).toBeVisible()
  await page.getByRole('button',{name:'章纲',exact:true}).click()
  await expect(floating).toBeVisible()
  await page.getByRole('button',{name:'展开工作流进度'}).click()
  await expect(logs).toContainText('正在补齐前文梗概')
  await page.reload()
  await expect(logs).toContainText('正在补齐前文梗概')
  await page.getByRole('button',{name:'章纲 1 死字号',exact:true}).click()
  await page.getByRole('button',{name:'生成正文',exact:true}).click()
  await expect(page).toHaveURL(new RegExp(`reader\\?chapter=${chapter.id}`))
  const version=await(await request.post(`${api}/chapters/${chapter.id}/versions`,{data:{base_version_id:chapter.current_version_id,title:'死字号',content:'　　陈渡抓住铁铲。\n\n　　石缝里吹来冷风。',source:'workflow'}})).json()
  job={...job,status:'completed',message:'正文已保存，待审阅修改',saved_version_id:version.id,has_output:true}
  await expect(floating).toContainText('正文已保存，待审阅修改',{timeout:10000})
  await expect(page.getByText('石缝里吹来冷风。',{exact:false}).first()).toBeVisible({timeout:10000})
  await expect(logs).toContainText('正文模型 · 运行中')
  await page.reload()
  await expect(logs).toContainText('正在补齐前文梗概')
  await expect(page.getByRole('button',{name:'工作流生成正文',exact:true})).toBeVisible()
  await page.screenshot({path:testInfo.outputPath('workflow-reader.png'),fullPage:true})
  const plans=await(await request.get(`${api}/books/${book.id}/chapter-plans`)).json()
  expect(plans.plans.find((p:{id:string})=>p.id===plan.id).chapter_id).toBe(chapter.id)
  expect((await(await request.get(`${api}/books/${book.id}/chapters`)).json()).length).toBe(1)
 }finally{await request.delete(`${api}/books/${book.id}`)}
})
