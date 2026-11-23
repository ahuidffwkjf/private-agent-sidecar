// No real model calls: execute the plugin against stub SDK boundaries.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const source=await readFile(new URL('../plugins/dsh-private-agent-sidecar/lib/index.js',import.meta.url),'utf8');
const requests=[];
const context=vm.createContext({URL,AbortController,setTimeout,clearTimeout,fetch:async(url,options)=>{
  requests.push({url,options});return {ok:true,status:200,text:async()=>JSON.stringify({task_id:'toy-test',source_hash:'hash',version:1})};
}});
const chain=new Proxy({}, {get:()=>()=>chain});
const schema=new vm.SyntheticModule(['default'],function(){this.setExport('default',{object:()=>chain,string:()=>chain,boolean:()=>chain,number:()=>chain});},{context});
const tools=new vm.SyntheticModule(['defineTool'],function(){this.setExport('defineTool',value=>value);},{context});
const plugin=new vm.SourceTextModule(source,{context});
await plugin.link(specifier=>specifier.includes('schemastery')?schema:tools);await plugin.evaluate();
const config={baseUrl:'http://127.0.0.1:8801',allowRemote:false,allowMutations:false,requestTimeoutMs:1000};
let registered=[];plugin.namespace.apply({tools:{register:t=>registered.push(t)}},config);
const publish=registered.find(t=>t.name==='sidecar_task_publish');
assert.ok(publish);assert.ok(registered.find(t=>t.name==='sidecar_task_summary'));
await assert.rejects(()=>publish.execute({title:'Task',original:'Original',acceptance:'Criteria',requestId:'r1'}),/disabled/);
assert.equal(requests.length,0);
registered=[];plugin.namespace.apply({tools:{register:t=>registered.push(t)}},{...config,allowMutations:true});
await registered.find(t=>t.name==='sidecar_task_publish').execute({title:'Task',original:'Original',acceptance:'Criteria',requestId:'r1'});
assert.equal(requests[0].url,'http://127.0.0.1:8801/v1/tasks');
assert.equal(JSON.parse(requests[0].options.body).request_id,'r1');
assert.equal(requests[1].url,'http://127.0.0.1:8801/v1/toy/trace');
assert.throws(()=>plugin.namespace.normalizeBaseUrl('http://remote.example',true),/HTTPS/);
console.log(JSON.stringify({checks:['registration','mutation-opt-in','publish-mapping','trace-hook','remote-TLS'],status:'passed'}));
