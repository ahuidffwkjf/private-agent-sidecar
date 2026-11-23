const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validateConnection,trustedNavigation,trustedLocalFileNavigation,backendCommand,validateReady}=require('../policy.cjs');
test('settings file URL permits equivalent tilde encoding but no other file or origin',()=>{
  const path=require('node:path');
  const {pathToFileURL}=require('node:url');
  const expected=pathToFileURL(path.resolve('RUNNER~1/resources/app.asar/settings.html')).href;
  assert(trustedLocalFileNavigation(expected,expected));
  assert(trustedLocalFileNavigation(expected.replace(/%7e/ig,'~'),expected));
  for(const url of [expected+'?x=1',expected+'#x',expected.replace('settings.html','preload.cjs'),expected.replace('file:','https:'),'file://other/settings.html','javascript:alert(1)',expected.replace('settings.html','%2fsettings.html')])assert(!trustedLocalFileNavigation(url,expected));
});
test('remote connections require TLS except exact loopback hosts',()=>{
  assert.deepEqual(validateConnection({mode:'local'}),{mode:'local'});
  assert.equal(validateConnection({mode:'remote',url:'https://example.com/ui'}).url,'https://example.com');
  assert.equal(validateConnection({mode:'remote',url:'http://127.0.0.1:8801'}).url,'http://127.0.0.1:8801');
  for(const url of ['http://example.com','http://127.0.0.1.evil.com','file:///etc/passwd','javascript:alert(1)','https://user:pass@example.com','https://example.com/?key=secret','https://example.com/#secret','https://example.com/prefix'])assert.throws(()=>validateConnection({mode:'remote',url}));
});
test('navigation cannot escape the chosen backend origin',()=>{
  assert(trustedNavigation('https://example.com/ui/admin','https://example.com'));
  for(const url of ['file:///etc/passwd','https://other.com','https://example.com.evil.com','https://user:pass@example.com','http://example.com'])assert(!trustedNavigation(url,'https://example.com'));
});
test('platform specific backend commands are argument arrays, never shell strings',()=>{
  for(const platform of ['darwin','linux','win32']){
    const installed=backendCommand({packaged:true,resources:'/resources',root:'/source',platform});
    assert(installed.file.endsWith(platform==='win32'?'SidecarBackend.exe':'SidecarBackend'));
    assert.deepEqual(installed.args,[]);
    const dev=backendCommand({packaged:false,resources:'/resources',root:'/source',platform});
    assert.deepEqual(dev.args,['-m','agent_sidecar.desktop']);
    assert(dev.file.endsWith(platform==='win32'?'python.exe':'python'));
  }
});
test('toy readiness only accepts four unique loopback users',()=>{
  const users=['Alice','Bob','Carol','Dave'].map((name,i)=>({name,url:`http://127.0.0.1:${9000+i}`}));
  assert.equal(validateReady({url:users[0].url,users}).users.length,4);
  assert.throws(()=>validateReady({url:users[0].url,users:users.slice(0,2)}));
  assert.throws(()=>validateReady({url:users[0].url,users:[users[0],users[0],users[2],users[3]]}));
  assert.throws(()=>validateReady({url:'https://other.example',users}));
  assert.throws(()=>validateReady({url:users[0].url,users:[...users.slice(0,3),{name:'Dave',url:'http://remote.example:9004'}]}));
});
