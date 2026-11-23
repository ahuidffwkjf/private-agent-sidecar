const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validateConnection,trustedNavigation,backendCommand}=require('../policy.cjs');
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
