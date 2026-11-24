'use strict';
const form=document.querySelector('#settings'),url=document.querySelector('#url'),error=document.querySelector('#error'),save=document.querySelector('#save');
const fields=document.querySelector('#connection-fields');
function toggle(){url.disabled=form.elements.mode.value!=='remote';url.required=!url.disabled;}
form.addEventListener('change',toggle);
const accessStatus=document.querySelector('#access-status'),importAccess=document.querySelector('#import-access');
window.connectionSettings.read().then(value=>{form.elements.mode.value=value.mode;url.value=value.url||'';if(accessStatus)accessStatus.textContent=value.ecsConfigured?'已导入接入授权':'未导入接入授权';toggle();fields.disabled=false;}).catch(e=>{error.textContent=e.message;});
if(importAccess)importAccess.addEventListener('click',async()=>{importAccess.disabled=true;try{const result=await window.connectionSettings.importAccess();accessStatus.textContent=result.configured?'已导入接入授权':'未导入接入授权';}catch(e){error.textContent=e.message;}finally{importAccess.disabled=false;}});
form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;error.textContent='';try{await window.connectionSettings.save({mode:form.elements.mode.value,url:url.value.trim()});}catch(e){error.textContent=e.message;}finally{save.disabled=false;}});
