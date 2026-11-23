// Run against an isolated preview: NODE_PATH=<packages> node scripts/check_chat_ui.mjs <url> <output>
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const base=process.argv[2];
const output=process.argv[3]||'runs/chat-ui-check';
if(!base)throw new Error('Pass the isolated preview URL');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
const errors=[];
const checks=[];
const projectName='界面验收讨论 '+Date.now();
const publicMessage='界面验收：公共消息已接入真实本地接口。'+Date.now();
const ok=name=>checks.push(name);
try {
  const context=await browser.newContext({viewport:{width:1440,height:940}});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  await page.locator('#connection-label').getByText('Sidecar 已连接',{exact:true}).waitFor();
  await page.locator('#message').fill(publicMessage);
  await page.locator('#send').click();
  await page.locator('.bubble').getByText(publicMessage,{exact:true}).waitFor();
  assert.equal(await page.locator('#message').inputValue(),'');
  ok('real-public-send');
  await page.locator('#add').click();
  await page.locator('#project-name').fill(projectName);
  await page.getByRole('button',{name:'创建讨论',exact:true}).click();
  await page.locator('#detail-title').getByText(projectName,{exact:true}).waitFor();
  await page.locator('#message').fill('项目讨论保持原有签名留言板与成员同步路径。');
  await page.locator('#send').click();
  await page.locator('.bubble').getByText('项目讨论保持原有签名留言板与成员同步路径。',{exact:true}).waitFor();
  ok('real-project-create-and-send');
  await page.locator('#message').fill('保留的项目草稿');
  await page.locator('[data-select="public"]').click();
  await page.getByRole('button',{name:new RegExp(projectName)}).click();
  assert.equal(await page.locator('#message').inputValue(),'保留的项目草稿');
  ok('drafts-isolated-by-conversation');
  await page.locator('#message').fill('');
  await page.locator('.bubble').getByText('项目讨论保持原有签名留言板与成员同步路径。',{exact:true}).waitFor();
  await page.screenshot({path:output+'/desktop-chat.png'});
  await page.locator('.nav-button[data-view="discover"]').click();
  await page.locator('[data-select="feed"]').click();
  await page.getByText(publicMessage,{exact:true}).waitFor();
  assert.equal(await page.locator('[data-select="moments"]').count(),0);
  ok('discovery-live-feed-and-honest-unavailable-state');
  await page.locator('.nav-button[data-view="skills"]').click();
  await page.locator('#search').fill('sidecar.project.create');
  await page.locator('[data-select="sidecar.project.create"]').click();
  const install=page.locator('[data-command="install"]');
  if(await install.isEnabled())await install.click();
  await page.getByText('已安装 · 原生通信工具',{exact:true}).waitFor();
  await page.locator('[data-command="manifest"]').click();
  assert.match(await page.locator('#dialog-body').innerText(),/sidecar.project.create/);
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  ok('real-skill-install-and-manifest');
  await page.screenshot({path:output+'/desktop-skills.png'});
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    await page.locator('.nav-button[data-view="chats"]').click();
    assert.equal(await page.locator('.list-pane').isVisible(),true);
    await page.getByRole('button',{name:new RegExp(projectName)}).click();
    await page.locator('.bubble').getByText('项目讨论保持原有签名留言板与成员同步路径。',{exact:true}).waitFor();
    assert.equal(await page.locator('.list-pane').isVisible(),false);
    assert.equal(await page.locator('#message').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const overflows=await page.evaluate(()=>[...document.querySelectorAll('.bubble,.main-header,.composer,.rail')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).length);
    assert.equal(overflows,0);
    await page.screenshot({path:`${output}/mobile-${width}.png`});
    await page.getByRole('button',{name:'返回列表',exact:true}).click();
    assert.equal(await page.locator('.list-pane').isVisible(),true);
  }
  ok('mobile-320-390-layout-and-back');
  await context.close();

  // Adversarial states are injected only in this test context, never into the user's node.
  const fixture=await browser.newContext({viewport:{width:1280,height:860}});
  const peer={agent_id:'agent:remote',display_name:'测试联系人',relationship_state:'mutual',outgoing_trust:true,direct:{endpoint:'https://example.invalid'},signing_key:'test-public-key'};
  const pending={agent_id:'agent:pending',display_name:'<img src=x onerror=alert(1)>',relationship_state:'pending',direct:{},signing_key:'pending-key'};
  let failSend=false, delayPrivate=false, queuedSend=false;
  const calls=[];
  await fixture.route('**/v1/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname;
    let data;
    if(path==='/v1/identity')data={agent_id:'agent:self'};
    if(path==='/v1/peers/candidates')data={peers:[peer,pending]};
    if(path==='/v1/projects')data={projects:[]};
    if(path==='/v1/peers/trust-notifications')data={notifications:[]};
    if(path==='/v1/project-invitations')data={incoming:[]};
    if(path==='/v1/private-messages'){
      if(delayPrivate)await new Promise(r=>setTimeout(r,800));
      data={messages:[{message_id:'remote-1',direction:'incoming',created_at:1800000000,payload:{text:'私聊隔离内容'}}]};
    }
    if(path==='/v1/broadcasts')data={broadcasts:[]};
    if(path==='/v1/messages/send'){
      calls.push(route.request().postDataJSON());
      if(failSend){await route.fulfill({status:400,json:{error:'直连不可达'}});return;}
      data={deliveries:[{status:queuedSend?'queued':'sent'}]};
    }
    if(path==='/v1/peers/trust-requests'){calls.push(route.request().postDataJSON());data={status:'pending'};}
    if(data!==undefined)await route.fulfill({json:data});else await route.continue();
  });
  const p=await fixture.newPage();p.on('pageerror',e=>errors.push(e.message));
  await p.goto(base);await p.locator('[data-select="private:agent:remote"]').waitFor();
  await p.locator('[data-select="private:agent:remote"]').click();
  await p.locator('#content').getByText('私聊隔离内容',{exact:true}).waitFor();
  await p.locator('#message').fill('私聊失败草稿');failSend=true;
  await p.locator('#send').click();await p.locator('#send-status').getByText('发送失败：直连不可达',{exact:true}).waitFor();
  assert.equal(await p.locator('#message').inputValue(),'私聊失败草稿');
  assert.equal(calls.at(-1).target.kind,'private');
  assert.equal(calls.at(-1).target.recipient_id,'agent:remote');
  ok('private-transport-failure-retains-draft-no-relay-fallback');
  failSend=false;queuedSend=true;
  await p.locator('#send').click();
  await p.locator('#send-status').getByText('已进入持久队列，等待投递',{exact:true}).waitFor();
  assert.equal(await p.locator('#message').inputValue(),'');
  ok('queued-is-not-displayed-as-delivered');
  await p.locator('[data-select="public"]').click();
  delayPrivate=true;
  await p.locator('[data-select="private:agent:remote"]').click();
  await p.locator('[data-select="public"]').click();
  await p.waitForTimeout(1000);
  assert.equal(await p.locator('#content').getByText('私聊隔离内容',{exact:true}).count(),0);
  ok('stale-private-response-does-not-leak-into-other-conversation');
  await p.locator('.nav-button[data-view="contacts"]').click();
  await p.locator('[data-select="agent:pending"]').click();
  assert.equal(await p.locator('#content img').count(),0);
  await p.locator('[data-command="trust"]').click();
  assert.match(await p.locator('#dialog-body').innerText(),/pending-key/);
  const before=calls.length;
  await p.locator('#cancel-dialog').click();assert.equal(calls.length,before);
  await p.locator('[data-command="trust"]').click();await p.locator('#confirm-dialog').click();
  await p.locator('#dialog').waitFor({state:'hidden'});
  assert.equal(calls.at(-1).expected_signing_key,'pending-key');
  ok('contact-xss-escaping-and-explicit-fingerprint-confirmation');
  assert.deepEqual(errors,[]);
  await writeFile(output+'/summary.json',JSON.stringify({checks,errors},null,2));
  console.log(JSON.stringify({checks,errors,output},null,2));
} finally {await browser.close();}
