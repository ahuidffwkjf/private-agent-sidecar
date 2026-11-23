'use strict';
const form=document.querySelector('#settings'),url=document.querySelector('#url'),error=document.querySelector('#error'),save=document.querySelector('#save');
function toggle(){url.disabled=form.elements.mode.value!=='remote';url.required=!url.disabled;}
form.addEventListener('change',toggle);
window.connectionSettings.read().then(value=>{form.elements.mode.value=value.mode;url.value=value.url||'';toggle();}).catch(e=>{error.textContent=e.message;});
form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;error.textContent='';try{await window.connectionSettings.save({mode:form.elements.mode.value,url:url.value.trim()});}catch(e){error.textContent=e.message;}finally{save.disabled=false;}});
