// Use only an isolated preview: installs every catalog item into that node.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const {expect}=require('playwright/test');
const [base, output='runs/skill-ui-check', permission]=process.argv.slice(2);
if(!base || permission!=='--isolated')throw new Error('Usage: <isolated-preview-url> <output> --isolated (installs all Skills)');
const origin=new URL(base).origin;
if(!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname))throw new Error('Only loopback previews are allowed');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
const report={checks:[],skills:[],errors:[],execution:'native-sidecar'};
try {
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  page.on('pageerror',error=>report.errors.push(error.message));
  const api=page.request;
  const catalogResponse=await api.get(origin+'/v1/skills/catalog');
  assert.equal(catalogResponse.status(),200);
  const catalog=await catalogResponse.json();
  report.count=catalog.count;
  const last=catalog.skills.at(-1);
  await page.goto(base);
  await page.locator('.nav-button[data-view="skills"]').click();
  await expect(page.locator('.skill-row')).toHaveCount(catalog.count);
  const list=page.locator('#list');

  for(const [width,height] of [[1280,800],[1440,600],[390,844],[320,568]]){
    await page.setViewportSize({width,height});
    await page.locator('.nav-button[data-view="skills"]').click();
    await list.evaluate(el=>{el.scrollTop=0;});
    const dimensions=await list.evaluate(el=>({height:el.clientHeight,total:el.scrollHeight,pane:el.parentElement.clientHeight}));
    assert.ok(dimensions.height>0 && dimensions.height<height,JSON.stringify(dimensions));
    assert.ok(dimensions.total>dimensions.height,'Long Skill list must have a scroll range');
    await list.hover();
    await page.mouse.wheel(0,500);
    await expect.poll(()=>list.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
    await page.mouse.wheel(0,100000);
    const lastRow=page.locator(`[data-select="${last.skill_id}"]`);
    await expect(lastRow).toBeInViewport();
    await expect(page.locator('#search')).toBeInViewport();
    await expect(page.locator('#category')).toBeInViewport();
    await expect(page.locator('.connection')).toBeInViewport();
    await page.screenshot({path:`${output}/skills-${width}-${height}.png`});
    await lastRow.click();
    await expect(page.locator('#detail-title')).toHaveText(last.name);
    await page.locator('[data-command="manifest"]').click();
    assert.equal(JSON.parse(await page.locator('#dialog-body pre').innerText()).skill_id,last.skill_id);
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    if(width<=640){
      await page.getByRole('button',{name:'返回列表',exact:true}).click();
      await expect(lastRow).toBeInViewport();
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    report.checks.push(`wheel-last-item-detail-manifest-${width}x${height}`);
  }

  await page.setViewportSize({width:1280,height:800});
  await page.locator('.nav-button[data-view="skills"]').click();
  for(const category of catalog.categories){
    await page.locator('#category').selectOption(category.name);
    await expect(page.locator('.skill-row')).toHaveCount(category.count);
  }
  await page.locator('#category').selectOption('');
  await page.locator('#search').fill(last.skill_id);
  await expect(page.locator('.skill-row')).toHaveCount(1);
  await page.locator('#search').fill('no-skill-matches-this-query');
  await expect(page.locator('.skill-row')).toHaveCount(0);
  await page.locator('#search').fill('');
  report.checks.push('every-category-and-search');

  // Exercise the visible detail/install/manifest controls for every catalog row.
  for(const skill of catalog.skills){
    await page.locator(`[data-select="${skill.skill_id}"]`).click();
    await expect(page.locator('#detail-title')).toHaveText(skill.name);
    const install=page.locator('[data-command="install"]');
    if(!skill.installed){
      const installedResponse=page.waitForResponse(r=>r.url().endsWith('/v1/skills/catalog/install') && r.request().method()==='POST');
      await install.click();
      assert.equal((await installedResponse).status(),201);
    }
    await expect(install).toHaveText('已安装');
    await expect(install).toBeDisabled();
    await page.locator('[data-command="manifest"]').click();
    const manifest=JSON.parse(await page.locator('#dialog-body pre').innerText());
    assert.equal(manifest.skill_id,skill.skill_id);
    assert.equal(manifest.installed,true);
    assert.equal(manifest.execution,'native-sidecar');
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    const actions=[];
    if(!skill.mutates && skill.input_schema.required.length===0){
      const response=await api.post(origin+'/mcp',{data:{jsonrpc:'2.0',id:1,method:'tools/call',params:{name:`skill.${skill.skill_id}`,arguments:{}}}});
      assert.equal(response.status(),200);
      const rpc=await response.json();
      assert.notEqual(rpc.result.isError,true);
      actions.push({action:skill.actions[0],status:'read-executed'});
    }
    report.skills.push({id:skill.skill_id,detail:'passed',install:'passed',manifest:'passed',actions});
    if(report.skills.length%50===0)console.log(`Checked ${report.skills.length}/${catalog.count} Skills`);
  }
  await page.reload();
  await page.locator('.nav-button[data-view="skills"]').click();
  await expect(page.locator('.skill-row')).toHaveCount(catalog.count);
  await page.locator('#installed').selectOption('installed');
  await expect(page.locator('.skill-row')).toHaveCount(catalog.count);
  const installed=await (await api.get(origin+'/v1/skills')).json();
  assert.equal(installed.skills.length,catalog.count);
  const tools=await (await api.post(origin+'/mcp',{data:{jsonrpc:'2.0',id:2,method:'tools/list'}})).json();
  const names=new Set(tools.result.tools.map(tool=>tool.name));
  for(const skill of catalog.skills)assert.ok(names.has(`skill.${skill.skill_id}`));
  report.checks.push('all-skills-detail-install-manifest-reload-mcp');
  await page.locator('[data-select="sidecar.node.identity"]').click();
  await page.locator('[data-command="run-skill"]').click();
  await page.getByRole('button',{name:'查询',exact:true}).click();
  await expect(page.locator('#skill-result')).toContainText('agent_id');
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  report.checks.push('native-tool-execution-from-ui');
  await page.locator('.nav-button[data-view="me"]').click();
  await page.locator('[data-select="actors"]').click();
  for(const name of ['Planner','Reviewer']){
    await page.locator('[data-command="actor-register"]').click();
    await page.locator('#actor-name').fill(name);
    await page.getByRole('button',{name:'注册',exact:true}).click();
    await expect(page.locator('#dialog')).not.toBeVisible();
    await expect(page.locator('#content h3').getByText(name,{exact:true})).toBeVisible();
  }
  await expect(page.locator('[data-actor-inbox]')).toHaveCount(2);
  await page.locator('[data-actor-inbox]').first().click();
  await expect(page.locator('#dialog-body')).toContainText('暂无消息');
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  await page.screenshot({path:output+'/actors-desktop.png'});
  await page.reload();
  await expect(page.locator('#connection-label')).toHaveText('Sidecar 已连接');
  await page.locator('.nav-button[data-view="me"]').click();
  await page.locator('[data-select="actors"]').click();
  await expect(page.locator('[data-actor-inbox]')).toHaveCount(2);
  report.checks.push('register-two-actors-inbox-and-reload');
  assert.deepEqual(report.errors,[]);
  console.log(JSON.stringify({count:report.count,checked:report.skills.length,actions:report.skills.reduce((n,s)=>n+s.actions.length,0),checks:report.checks,errors:report.errors},null,2));
} catch(error){
  report.failure=error.stack;
  throw error;
} finally {
  await writeFile(output+'/summary.json',JSON.stringify(report,null,2)+'\n');
  await browser.close();
}
