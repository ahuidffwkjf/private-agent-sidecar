const {_electron}=require('playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const readline=require('node:readline');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const release=process.env.SIDECAR_DESKTOP_RELEASE?path.resolve(process.env.SIDECAR_DESKTOP_RELEASE):path.join(root,'desktop/release');
const metadata=require('../package.json');
const folder=process.platform==='darwin'?(process.arch==='arm64'?'mac-arm64':'mac'):process.platform==='win32'?'win-unpacked':'linux-unpacked';
const executable=process.env.SIDECAR_DESKTOP_EXECUTABLE||path.join(release,folder,...(process.platform==='darwin'?[metadata.productName+'.app','Contents/MacOS',metadata.productName]:[process.platform==='win32'?metadata.productName+'.exe':metadata.name]));
async function main(){
  const started=Date.now(),directory=fs.mkdtempSync(path.join(os.tmpdir(),'ecs-native-test-'));
  const python=process.env.SIDECAR_TEST_PYTHON||(process.platform==='win32'?'python':'python3');
  const fixture=spawn(python,[path.join(root,'scripts/ecs_toy_fixture.py')],{stdio:['pipe','pipe','inherit']});
  let a,b,unconfigured;
  const checks=[],errors=[];
  try{
    const ready=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('TLS fixture startup timeout')),20000);const lines=readline.createInterface({input:fixture.stdout});lines.once('line',line=>{clearTimeout(timer);lines.close();resolve(JSON.parse(line));});fixture.once('exit',()=>{clearTimeout(timer);reject(Error('TLS fixture exited'));});});
    assert.equal(ready.synthetic,true);
    const fresh=path.join(directory,'fresh');fs.mkdirSync(fresh);
    const freshEnv={...process.env,SIDECAR_SMOKE_DATA_DIR:fresh};delete freshEnv.ELECTRON_RUN_AS_NODE;
    unconfigured=await _electron.launch({executablePath:executable,env:freshEnv,timeout:90000});
    const until=Date.now()+30000;let settings;
    while(Date.now()<until){settings=unconfigured.windows().find(p=>p.url().endsWith('/settings.html'));if(settings)break;await new Promise(resolve=>setTimeout(resolve,100));}
    assert(settings,'first launch must show access provisioning');
    await settings.locator('input[value=ecs]').waitFor();assert(await settings.locator('input[value=ecs]').isChecked());
    assert.equal(fs.existsSync(path.join(fresh,'workspace')),false);
    await unconfigured.close();unconfigured=null;
    checks.push('first-launch-requires-access-no-silent-demo');
    async function start(name){
      const data=path.join(directory,name);fs.mkdirSync(data,{recursive:true});
      fs.copyFileSync(ready.access_file,path.join(data,'ecs-access.private.json'));
      const env={...process.env,SIDECAR_SMOKE_DATA_DIR:data};delete env.ELECTRON_RUN_AS_NODE;
      const app=await _electron.launch({executablePath:executable,env,timeout:90000});
      const page=await app.firstWindow();page.on('pageerror',error=>errors.push(error.message));
      await page.waitForURL('**/ui?desktop=1&toy=1&online=1',{timeout:90000});
      return {app,page};
    }
    async function consent(page,name){await page.locator('#toy-nickname').fill(name);await page.locator('#toy-agree').check();await page.getByRole('button',{name:'确认并进入实验',exact:true}).click();await page.locator('[data-toy=publish]').waitFor();}
    async function api(page,endpoint,body){return page.evaluate(async({endpoint,body})=>{const r=await fetch(endpoint,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const v=await r.json();if(!r.ok)throw Error(v.error);return v;},{endpoint,body});}
    let first=await start('a');a=first.app;const pageA=first.page;
    let second=await start('b');b=second.app;let pageB=second.page;
    assert.equal(await a.evaluate(({Menu})=>Menu.getApplicationMenu().items.some(i=>i.label==='演示用户')),false);
    checks.push('defaults-to-ecs-not-four-user-demo','native-single-user-backend');
    await consent(pageA,'ECS Alice');await consent(pageB,'ECS Bob');
    const identityB=(await api(pageB,'/v1/identity')).agent_id;
    await pageA.locator('[data-toy=publish]').click();
    await pageA.locator('[name=title]').fill('ECS 在线邀请验收');
    await pageA.locator('[name=original]').fill('只使用合成内容完成一次邀请和群聊。');
    await pageA.locator('[name=acceptance]').fill('受邀人明确接受后加入。');
    await pageA.locator('#toy-task-form button[type=submit]').click();
    await pageA.locator('[data-toy=invite]').waitFor();
    await pageA.locator('[data-toy=invite]').click();
    await pageA.locator('#toy-invite-node').selectOption(identityB);
    await pageA.locator('#toy-invite-confirm').click();
    await pageB.locator('.task-row').filter({hasText:'ECS 在线邀请验收'}).locator('[data-toy=detail]').click();
    await pageB.locator('[data-toy=accept_invite]').waitFor();
    checks.push('online-discovery-and-directed-task-invitation','not-joined-before-explicit-acceptance');
    await pageB.locator('[data-toy=accept_invite]').click();await pageB.locator('#toy-confirm').click();
    await pageB.locator('[data-toy=group]').waitFor({timeout:60000});await pageB.locator('[data-toy=group]').click();
    await pageB.locator('#message').fill('ECS 密文群消息验收');await pageB.locator('#send').click();
    const task=(await api(pageA,'/v1/tasks')).tasks.find(t=>t.title==='ECS 在线邀请验收');
    await pageA.locator('[data-toy=group]').click();
    await pageA.locator('#content').getByText('ECS 密文群消息验收',{exact:true}).waitFor({timeout:60000});
    checks.push('accepted-membership-delivered-via-encrypted-relay','group-message-received-without-direct-listener');
    await pageA.screenshot({path:path.join(release,'ecs-desktop.png')});
    for(const width of [390,320]){
      await a.evaluate(({BrowserWindow},width)=>{const w=BrowserWindow.getAllWindows()[0];w.setMinimumSize(320,500);w.setSize(width,844);},width);
      await pageA.setViewportSize({width,height:780});
      assert(await pageA.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await pageA.screenshot({path:path.join(release,`ecs-${width}.png`)});
    }
    checks.push('390-and-320-responsive-no-horizontal-overflow');
    await b.close();b=null;second=await start('b');b=second.app;pageB=second.page;
    assert.equal((await api(pageB,'/v1/identity')).agent_id,identityB);
    assert.equal((await api(pageB,'/v1/tasks/'+task.task_id)).group_ready,true);
    assert.equal((await api(pageB,'/v1/toy/profile')).research.consent_active,true);
    checks.push('restart-preserves-real-identity-consent-and-membership');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(release,'online-desktop-smoke.json'),JSON.stringify({status:'passed',version:metadata.version,platform:process.platform,arch:process.arch,checks,errors,elapsed_ms:Date.now()-started,fixture:'synthetic local TLS Relay, not public ECS acceptance'},null,2));
    console.log(JSON.stringify({status:'passed',checks,elapsed_ms:Date.now()-started}));
  }finally{
    if(unconfigured)await unconfigured.close();if(a)await a.close();if(b)await b.close();fixture.stdin.end();
    await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();fixture.once('exit',resolve);setTimeout(()=>{fixture.kill();resolve();},5000).unref();});
    fs.rmSync(directory,{recursive:true,force:true,maxRetries:5,retryDelay:300});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
