const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('connection settings stay disabled until persisted configuration is loaded',async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../settings.html'),'utf8');
  assert.match(html,/<fieldset[^>]*id="connection-fields"[^>]*disabled/);
  let resolve;
  const pending=new Promise(done=>{resolve=done;});
  const handlers={};
  const elements={
    '#settings':{elements:{mode:{value:'local'}},addEventListener:(name,fn)=>{handlers[name]=fn;}},
    '#connection-fields':{disabled:true},'#url':{value:'',disabled:true},
    '#error':{textContent:''},'#save':{disabled:false}
  };
  const context={document:{querySelector:selector=>elements[selector]},window:{connectionSettings:{read:()=>pending}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../settings.js'),'utf8'),context);
  assert.equal(elements['#connection-fields'].disabled,true);
  resolve({mode:'remote',url:'https://sidecar.example.com'});
  await pending;
  await Promise.resolve();
  assert.equal(elements['#connection-fields'].disabled,false);
  assert.equal(elements['#settings'].elements.mode.value,'remote');
  assert.equal(elements['#url'].value,'https://sidecar.example.com');
  assert.equal(elements['#url'].disabled,false);
});
