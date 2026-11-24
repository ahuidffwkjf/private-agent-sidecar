const {_electron}=require('playwright-core');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');
const release=process.env.SIDECAR_DESKTOP_RELEASE?path.resolve(process.env.SIDECAR_DESKTOP_RELEASE):path.resolve(__dirname,'../release');
const metadata=require('../package.json');
function executable(){
  if(process.env.SIDECAR_DESKTOP_EXECUTABLE)return path.resolve(process.env.SIDECAR_DESKTOP_EXECUTABLE);
  const folder=process.platform==='darwin'?(process.arch==='arm64'?'mac-arm64':'mac'):process.platform==='win32'?'win-unpacked':'linux-unpacked';
  return path.join(release,folder,...(process.platform==='darwin'?[metadata.productName+'.app','Contents','MacOS',metadata.productName]:process.platform==='win32'?[metadata.productName+'.exe']:[metadata.name]));
}
async function run(){
  const started=performance.now();
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'sidecar-toy-smoke-'));
  fs.writeFileSync(path.join(data,'connection.json'),JSON.stringify({mode:'local'}));
  const env={...process.env,SIDECAR_SMOKE_DATA_DIR:data};
  delete env.ELECTRON_RUN_AS_NODE;delete env.PYTHONPATH;delete env.PYTHONHOME;
  let application;
  const checks=[],errors=[];
  const urls=new Set();
  async function start(){
    application=await _electron.launch({executablePath:executable(),env,timeout:90000});
    const page=await application.firstWindow();page.on('pageerror',e=>errors.push(e.message));
    await page.waitForURL('**/ui?desktop=1&toy=1',{timeout:90000});
    await page.locator('#connection-label').getByText('Sidecar 已连接',{exact:true}).waitFor();
    return page;
  }
  async function switchUser(page,name){
    const before=page.url();
    await application.evaluate(({Menu},target)=>Menu.getApplicationMenu().items.find(i=>i.label==='演示用户').submenu.items.find(i=>i.label===target).click(),name);
    await page.waitForURL(url=>url.href!==before,{timeout:30000});
    await page.locator('#connection-label').getByText('Sidecar 已连接',{exact:true}).waitFor();
    urls.add(new URL(page.url()).origin);
  }
  async function consent(page,name){
    await page.locator('#toy-nickname').waitFor();
    await page.locator('#toy-nickname').fill(name+' 演示用户');await page.locator('#toy-agree').check();
    await page.getByRole('button',{name:'确认并进入实验',exact:true}).click();
    await page.locator('[data-toy=publish]').waitFor();
  }
  async function api(page,path,body){
    return page.evaluate(async({path,body})=>{const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw Error(value.error);return value;},{path,body});
  }
  try{
    let page=await start();urls.add(new URL(page.url()).origin);
    const prefs=await application.evaluate(({BrowserWindow})=>{const w=BrowserWindow.getAllWindows()[0],p=w.webContents.getLastWebPreferences();return {minimum:w.getMinimumSize(),nodeIntegration:p.nodeIntegration,contextIsolation:p.contextIsolation,sandbox:p.sandbox};});
    assert.deepEqual(prefs,{minimum:[920,620],nodeIntegration:false,contextIsolation:true,sandbox:true});
    assert.equal(await page.evaluate(()=>typeof window.require),'undefined');assert.equal(await page.evaluate(()=>document.cookie),'');
    assert.equal((await fetch(new URL(page.url()).origin+'/v1/identity')).status,401);
    checks.push('frozen-backend-in-native-app','sandbox-httponly-auth','starts-at-toy-consent');
    await consent(page,'Alice');
    const alice=(await api(page,'/v1/identity')).agent_id;
    await page.locator('[data-toy=publish]').click();
    await page.locator('[name=title]').fill('原生应用验收任务');
    await page.locator('[name=original]').fill('请整理三条使用步骤，交给另一个演示用户验收。');
    await page.locator('[name=acceptance]').fill('三个清晰步骤；合成测试，不含真实信息。');
    await page.locator('#toy-task-form button[type=submit]').click();
    await page.locator('#detail-title').getByText('原生应用验收任务',{exact:true}).waitFor();
    const task=(await api(page,'/v1/tasks')).tasks.find(t=>t.title==='原生应用验收任务');assert(task);
    await page.locator('text=演示摘要 · 非模型生成').waitFor();
    checks.push('native-publish-and-demo-summary');
    await switchUser(page,'Bob');await consent(page,'Bob');
    await page.locator('.task-row').filter({hasText:'原生应用验收任务'}).locator('[data-toy=detail]').click();
    await page.locator('[data-toy=apply]').click();await page.locator('#toy-confirm').click();await page.getByText('申请中',{exact:true}).waitFor();
    await switchUser(page,'Alice');
    await page.locator('.task-row').filter({hasText:'原生应用验收任务'}).locator('[data-toy=detail]').click();
    await page.locator('[data-toy=approve]').click();await page.locator('#toy-confirm').click();
    await switchUser(page,'Bob');
    await page.locator('.task-row').filter({hasText:'原生应用验收任务'}).locator('[data-toy=detail]').click();
    await page.locator('[data-toy=group]').waitFor({timeout:45000});await page.locator('[data-toy=group]').click();
    await page.locator('#message').fill('双击启动的 Toy 应用群聊测试。');await page.locator('#send').click();
    await page.locator('.bubble').getByText('双击启动的 Toy 应用群聊测试。',{exact:true}).waitFor();
    await page.screenshot({path:path.join(release,'toy-desktop-chat.png')});
    checks.push('native-user-menu-switch','native-application-approval-group-message');
    await switchUser(page,'Carol');await page.locator('#toy-nickname').waitFor();
    assert.equal((await api(page,'/v1/toy/profile')).research.consent_active,false);
    await switchUser(page,'Dave');await page.locator('#toy-nickname').waitFor();
    checks.push('all-four-local-users','no-auto-consent-for-other-users');
    const oldUrls=[...urls];await application.close();application=null;
    for(const url of oldUrls)await assert.rejects(fetch(url+'/healthz'));
    checks.push('exit-releases-all-user-listeners');
    page=await start();
    await page.locator('[data-toy=publish]').waitFor();
    assert.equal((await api(page,'/v1/identity')).agent_id,alice);
    assert((await api(page,'/v1/tasks')).tasks.some(t=>t.task_id===task.task_id));
    checks.push('restart-preserves-identities-consent-and-tasks');
    await page.locator('.task-row').filter({hasText:'原生应用验收任务'}).locator('[data-toy=detail]').click();
    await page.screenshot({path:path.join(release,'toy-desktop-task.png')});
    await switchUser(page,'Bob');
    await page.locator('.task-row').filter({hasText:'原生应用验收任务'}).locator('[data-toy=detail]').click();
    await page.locator('[data-toy=group]').waitFor({timeout:45000});await page.locator('[data-toy=group]').click();
    await page.locator('.bubble').getByText('双击启动的 Toy 应用群聊测试。',{exact:true}).waitFor();
    checks.push('group-history-after-app-restart');
    await page.locator('#message').fill('重启后的新消息。');await page.locator('#send').click();
    await page.locator('.bubble').getByText('重启后的新消息。',{exact:true}).waitFor();
    await switchUser(page,'Alice');
    await page.locator('.task-row').filter({hasText:'原生应用验收任务'}).locator('[data-toy=detail]').click();
    await page.locator('[data-toy=group]').click();
    await page.locator('.bubble').getByText('重启后的新消息。',{exact:true}).waitFor({timeout:30000});
    checks.push('fresh-peer-delivery-after-port-reassignment');
    const catalog=await api(page,'/v1/skills/catalog');assert.equal(catalog.count,23);
    checks.push('communication-catalog-preserved');
    const newWindow=application.waitForEvent('window');
    await application.evaluate(({Menu})=>Menu.getApplicationMenu().items.find(x=>x.label==='文件').submenu.items.find(x=>x.label==='连接设置…').click());
    const settings=await newWindow;await settings.locator('input[value=remote]').check();await settings.locator('#url').fill('http://example.com');await settings.locator('#save').click();await settings.getByText(/远程服务必须使用 HTTPS/).waitFor();
    checks.push('settings-ipc-still-requires-remote-tls');
    assert.deepEqual(errors,[]);
    const result={platform:process.platform,arch:process.arch,version:metadata.version,checks,errors,status:'passed',elapsed_ms:Math.round(performance.now()-started),config:{users:4,active_test_users:2,relay:'owned-loopback',collector:'owned-loopback',backend:'single-frozen-process',scenario:'publish-approve-group-restart-send'},summary:'explicit demo, no real model call'};
    fs.writeFileSync(path.join(release,'toy-desktop-smoke.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
  }catch(error){
    if(application){
      for(const [index,page] of application.windows().entries()){
        console.error('Failure window',index,page.url(),await page.locator('body').innerText().catch(()=>'(unavailable)'));
        await page.screenshot({path:path.join(release,`failed-smoke-${index}.png`)}).catch(()=>{});
      }
    }
    throw error;
  }finally{
    if(application)await application.close();
    fs.rmSync(data,{recursive:true,force:true});
  }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
