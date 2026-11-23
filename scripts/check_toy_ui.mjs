import {createRequire} from 'node:module';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const workspace=JSON.parse(await readFile(process.argv[2],'utf8'));
const output=process.argv[3];
if(!output)throw Error('Pass workspace.json and output directory');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const errors=[],checks=[];
try{
  const pages=[];
  for(const user of workspace.users.slice(0,2)){
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    const page=await context.newPage();pages.push(page);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(user.url+'/ui');
    await page.locator('#connection-label').getByText('Sidecar 已连接',{exact:true}).waitFor();
    await page.locator('.nav-button[data-view=discover]').click();
    await page.locator('[data-select=tasks]').click();
    if(await page.locator('#toy-nickname').count()){
      await page.locator('#toy-nickname').fill(user.name+' UI');
      await page.locator('#toy-agree').check();
      await page.getByRole('button',{name:'确认并进入实验',exact:true}).click();
    }
    await page.locator('[data-toy=publish]').waitFor();
    assert.match(await page.locator('#toy-banner').innerText(),/私聊、群聊/);
  }
  checks.push('explicit-collection-consent-and-banner');
  const [a,b]=pages;
  const title='UI任务 '+Date.now();
  await a.locator('[data-toy=publish]').click();
  await a.locator('#toy-task-form [name=title]').fill(title);
  await a.locator('#toy-task-form [name=original]').fill('比较两种实现方案，列出优点与局限，并提供来源。'+ '长文本用于响应式检查。'.repeat(25));
  await a.locator('#toy-task-form [name=acceptance]').fill('可读比较表与来源说明');
  await a.locator('#toy-task-form button[type=submit]').click();
  await a.locator('#detail-title').getByText(title,{exact:true}).waitFor();
  await a.locator('text=演示摘要 · 非模型生成').waitFor();
  checks.push('publish-and-explicit-demo-summary');
  await b.locator('#refresh').click();
  await b.locator('.task-row').filter({hasText:title}).locator('[data-toy=detail]').click();
  await b.locator('[data-toy=apply]').click();
  await b.locator('#toy-confirm').click();
  await b.getByText('申请中',{exact:true}).waitFor();
  await a.locator('#refresh').click();
  await a.locator('[data-toy=approve]').click();
  await a.locator('#toy-confirm').click();
  await b.locator('[data-toy=group]').waitFor({timeout:30000});
  checks.push('application-approval-real-group');
  await b.locator('[data-toy=group]').click();
  await b.locator('#message').fill('UI 项目群消息');await b.locator('#send').click();
  await b.locator('.bubble').getByText('UI 项目群消息',{exact:true}).waitFor();
  await b.locator('#message').fill('未发送草稿不采集');
  await a.screenshot({path:output+'/desktop-task.png'});
  await b.screenshot({path:output+'/desktop-group.png'});
  checks.push('group-message-and-draft');
  for(const width of [390,320]){
    await a.setViewportSize({width,height:844});
    await a.screenshot({path:output+`/task-${width}.png`});
    assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await a.locator('[data-toy=hall]').click();
    await a.locator('#task-search').fill(title);
    await a.locator('#task-search').blur();
    await a.screenshot({path:output+`/hall-${width}.png`});
    await a.locator('.task-row').filter({hasText:title}).locator('[data-toy=detail]').click();
  }
  checks.push('responsive-390-320-and-search');
  assert.deepEqual(errors,[]);
  await writeFile(output+'/summary.json',JSON.stringify({checks,errors,title},null,2));
  console.log(JSON.stringify({checks,errors}));
}finally{await browser.close();}
