const {_electron}=require('playwright-core');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');
const release=path.resolve(__dirname,'../release');
const platform=process.platform;
function executable(){
  const files=fs.readdirSync(release,{withFileTypes:true});
  const folder=files.find(entry=>entry.isDirectory()&&(platform==='darwin'?entry.name.startsWith('mac'):platform==='win32'?entry.name==='win-unpacked':entry.name==='linux-unpacked'));
  if(!folder)throw new Error('Build the unpacked desktop application first (npm run pack)');
  return path.join(release,folder.name,...(platform==='darwin'?['Sidecar.app','Contents','MacOS','Sidecar']:platform==='win32'?['Sidecar.exe']:['sidecar-desktop']));
}
async function run(){
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'sidecar-desktop-smoke-'));
  let application;
  const errors=[];
  const checks=[];
  const env={...process.env,SIDECAR_SMOKE_DATA_DIR:data};delete env.ELECTRON_RUN_AS_NODE;
  const start=async()=>{
    application=await _electron.launch({executablePath:executable(),env,timeout:60000});
    const page=await application.firstWindow();page.on('pageerror',e=>errors.push(e.message));
    await page.waitForURL('**/ui?desktop=1',{timeout:60000});
    await page.locator('#connection-label').getByText('Sidecar 已连接',{exact:true}).waitFor();
    return page;
  };
  try {
    let page=await start();
    const settings=await application.evaluate(({BrowserWindow})=>{
      const w=BrowserWindow.getAllWindows()[0],p=w.webContents.getLastWebPreferences();
      return {minimum:w.getMinimumSize(),nodeIntegration:p.nodeIntegration,contextIsolation:p.contextIsolation,sandbox:p.sandbox};
    });
    assert.deepEqual(settings,{minimum:[920,620],nodeIntegration:false,contextIsolation:true,sandbox:true});
    assert.equal(await page.evaluate(()=>typeof window.require),'undefined');
    assert.equal(await page.evaluate(()=>document.cookie),'');
    const origin=new URL(page.url()).origin;
    assert.equal((await fetch(origin+'/v1/identity')).status,401);
    checks.push('packaged-desktop-starts-without-source-python','sandbox-and-httponly-session','desktop-window-minimum-size');
    await page.locator('#message').fill('桌面版验收：重启后仍保留这条消息。');
    await page.locator('#send').click();
    await page.locator('.bubble').getByText('桌面版验收：重启后仍保留这条消息。',{exact:true}).waitFor();
    await page.screenshot({path:path.join(release,'desktop-smoke.png')});
    const id=await page.evaluate(async()=>{const r=await fetch('/v1/identity');return (await r.json()).agent_id;});
    await application.close();application=null;
    await assert.rejects(fetch(origin+'/ui'));
    checks.push('exit-stops-owned-backend');
    page=await start();
    await page.locator('.bubble').getByText('桌面版验收：重启后仍保留这条消息。',{exact:true}).waitFor();
    assert.equal(await page.evaluate(async()=>{const r=await fetch('/v1/identity');return (await r.json()).agent_id;}),id);
    checks.push('identity-and-chat-survive-restart');
    const catalog=await page.evaluate(async()=>{const r=await fetch('/v1/skills/catalog');return r.json();});
    assert.equal(catalog.count,catalog.skills.length);
    assert.equal(catalog.count,23);
    assert.equal(catalog.schema,'sidecar-communication-skill-catalog/1');
    assert(catalog.skills.every(s=>s.skill_id.startsWith('sidecar.')&&s.execution==='native-sidecar'));
    checks.push('only-23-native-communication-skills');
    await page.locator('.nav-button[data-view="skills"]').click();
    for(const candidate of catalog.skills){
      await page.locator('#search').fill(candidate.skill_id);
      await page.locator(`[data-select="${candidate.skill_id}"]`).click();
      const install=page.locator('[data-command="install"]');
      if(await install.isEnabled())await install.click();
      await page.waitForFunction(async id=>{const r=await fetch('/v1/skills/catalog');return (await r.json()).skills.some(s=>s.skill_id===id&&s.installed);},candidate.skill_id);
      assert.equal(await install.isEnabled(),false);
    }
    const installed=await page.evaluate(async()=>{const r=await fetch('/v1/skills/catalog');return r.json();});
    assert.equal(installed.installed_count,23);
    const tools=await page.evaluate(async()=>{const r=await fetch('/v1/mcp/manifest');return (await r.json()).tools.map(t=>t.name);});
    assert(catalog.skills.every(s=>tools.includes('skill.'+s.skill_id)));
    assert(!tools.some(name=>name.startsWith('skill.wechat.')));
    const retiredStatus=await page.evaluate(async()=>{
      const r=await fetch('/v1/skills/catalog/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_ids:['wechat.group.create']})});return r.status;
    });
    assert.equal(retiredStatus,400);
    checks.push('all-23-installed-through-ui-and-exposed-to-mcp','retired-wechat-catalog-rejected');
    const newWindow=application.waitForEvent('window');
    await application.evaluate(({Menu})=>{const file=Menu.getApplicationMenu().items.find(x=>x.label==='文件');file.submenu.items.find(x=>x.label==='连接设置…').click();});
    const settingsPage=await newWindow;
    await settingsPage.locator('input[value="remote"]').check();
    await settingsPage.locator('#url').fill('http://example.com');
    await settingsPage.locator('#save').click();
    await settingsPage.getByText(/远程服务必须使用 HTTPS/).waitFor();
    checks.push('settings-ipc-rejects-plaintext-remote-service');
    assert.deepEqual(errors,[]);
    const result={platform,arch:process.arch,checks,errors,catalog_schema:catalog.schema,skill_count:catalog.count,status:'passed'};
    fs.writeFileSync(path.join(release,'desktop-smoke.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  } finally {
    if(application)await application.close();
    fs.rmSync(data,{recursive:true,force:true});
  }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
