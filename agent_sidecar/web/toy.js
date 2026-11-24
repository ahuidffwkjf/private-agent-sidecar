'use strict';

window.SidecarToy = (() => {
  let host, profile = {mode:'standard'}, people = [], tasks = [], selected = 'tasks', filter = 'all', statusFilter = 'all', query = '';
  const labels = {open:'招募中',offered:'等待负责人接受',completed:'已完成',cancelled:'已取消',pending:'申请中',approved:'加入处理中',joined:'已加入',rejected:'已拒绝',withdrawn:'已撤回',invited:'收到邀请',left:'已退出'};
  const $ = selector => document.querySelector(selector);
  const me = () => profile.profile?.node_id;
  const esc = value => host.esc(value);
  const cmd = (name, label, data='') => `<button class="secondary" data-toy="${name}" ${data}>${label}</button>`;
  const list = values => `<ul>${values.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`;
  function init(value) { host=value; document.addEventListener('click',click); }
  async function refresh() {
    profile=await host.api('/v1/toy/profile');
    let banner=$('#toy-banner');
    if(!banner){banner=document.createElement('div');banner.id='toy-banner';banner.className='toy-banner';$('.main-pane').prepend(banner);}
    banner.hidden=profile.mode!=='toy';
    banner.textContent=profile.research?.consent_active?'实验采集模式 · 本应用私聊、群聊及 Harness 可见交互会上传至研究数据库':'Toy 实验 · 尚未确认内容采集';
    if(profile.mode==='toy'&&profile.research?.consent_active){tasks=(await host.api('/v1/tasks')).tasks||[];people=(await host.api('/v1/toy/users')).profiles||[];}
    else tasks=[];
  }
  function summary(task) {
    const value=task.summary||{};
    if(value.status==='restricted')return '<p>仅可查看此分支的层级与进度</p>';
    if(value.status==='ready')return `<p class="task-goal">${esc(value.content.goal)}</p>${list(value.content.todos)}<small>${value.source?.startsWith('demo')?'演示摘要 · 非模型生成':'Harness 摘要'}</small>`;
    return `<p>${esc(task.original.slice(0,200))}</p><span class="badge">${value.status==='failed'?'摘要生成失败':'摘要生成中'}</span>`;
  }
  function show(id='tasks') { selected=id;host.selectTask(id); }
  function render(id='tasks', force=false) {
    if(!force&&id===selected&&document.activeElement?.closest('#content')&&document.activeElement.matches('input,textarea,select'))return;
    selected=id;$('#composer').hidden=true;$('#detail-action').hidden=true;
    if(profile.mode!=='toy') {host.heading('任务大厅','尚未配置实验节点');host.setContent('<section class="page-section"><p>此节点未启用 Toy 实验。</p></section>',true);return;}
    if(!profile.research?.consent_active){renderConsent();return;}
    if(id==='toy-profile'){renderConsent();return;}
    if(id.startsWith('task:')){renderTask(tasks.find(t=>t.task_id===id.slice(5)));return;}
    host.heading('广播大厅','无报酬协作任务');
    const filtered=tasks.filter(t=>!t.parent_id&&(filter==='all'||filter==='mine'&&t.owner_id===me()||filter==='joined'&&t.is_member))
      .filter(t=>statusFilter==='all'||t.status===statusFilter).filter(t=>(t.title+' '+t.original).toLowerCase().includes(query.toLowerCase()));
    host.setContent(`<section class="page-section toy-page"><div class="page-actions">${cmd('publish',host.icon('plus')+' 发布任务')}${cmd('profile',host.icon('user-round')+' '+esc(profile.profile?.nickname||'实验设置'))}</div><div class="task-filters"><input id="task-search" aria-label="搜索任务" placeholder="搜索任务" value="${esc(query)}"><select id="task-filter" aria-label="任务范围"><option value="all">全部任务</option><option value="mine">我发布的</option><option value="joined">我参与的</option></select><select id="task-status" aria-label="任务状态"><option value="all">全部状态</option><option value="open">招募中</option><option value="completed">已完成</option><option value="cancelled">已取消</option></select></div><div class="task-list">${filtered.map(t=>`<article class="task-row"><div class="task-row-heading"><h3>${esc(t.title)}</h3><span class="badge">${labels[t.status]||esc(t.status)}</span></div><p class="muted">${esc(t.owner_name)} · ${new Date(t.created_at*1000).toLocaleDateString('zh-CN')}</p>${summary(t)}${cmd('detail','查看任务',`data-id="${esc(t.task_id)}"`)}</article>`).join('')||'<p class="empty-small">暂无匹配任务</p>'}</div></section>`,true);
    $('#task-filter').value=filter;$('#task-status').value=statusFilter;
    $('#task-filter').onchange=e=>{filter=e.target.value;render();};$('#task-status').onchange=e=>{statusFilter=e.target.value;render();};
    $('#task-search').onchange=e=>{query=e.target.value;render();};
  }
  function renderConsent(){
    host.heading('实验用户与采集','一位用户一个节点');
    host.setContent(`<section class="page-section toy-page"><h3>内容采集告知</h3><p>本实验记录任务、信任操作、本应用私聊和群聊正文，以及接入 Harness 提供的可见请求、响应和工具调用。数据集中保存在研究数据库，仅实验管理员可查询与导出，保留至管理员手动清理。</p><p>不记录未发送草稿、其他应用聊天、凭证或模型隐藏推理。退出后停止新增上传，历史数据不会自动删除；可联系实验管理员删除。</p><form id="toy-consent"><label for="toy-nickname">昵称</label><input id="toy-nickname" maxlength="80" required value="${esc(profile.profile?.nickname||'')}"><label class="choice"><input id="toy-agree" type="checkbox" required ${profile.research?.consent_active?'checked':''}>我已了解并同意上述内容采集</label><button class="primary" type="submit">${profile.research?.consent_active?'保存':'确认并进入实验'}</button></form><p>待上传 ${profile.research?.counts?.queued||0} 条 · ${esc(profile.research?.error||'采集连接正常')}</p>${profile.research?.consent_active?cmd('withdraw-consent','退出采集'):''}<p id="toy-error" class="error" hidden></p></section>`,true);
    $('#toy-consent').onsubmit=async e=>{e.preventDefault();await run(async()=>{await host.api('/v1/toy/profile',{nickname:$('#toy-nickname').value,consent_active:true});await refresh();render();});};
  }
  function renderTask(task){
    if(!task){host.heading('任务不可见','权限或状态已改变');host.setContent('<p class="empty-small">任务不存在或尚未同步</p>',true);return;}
    const own=task.owner_id===me(), app=task.applications?.[me()], active=!['completed','cancelled'].includes(task.status);
    const children=tasks.filter(t=>t.parent_id===task.task_id);
    host.heading(task.title,`${task.parent_id?'子任务':'主任务'} · ${labels[task.status]||task.status}`);
    let buttons=cmd('hall',host.icon('arrow-left')+' 大厅');
    if(task.group_ready)buttons+=cmd('group','进入项目群',`data-project="${esc(task.project_id)}"`);
    if(active){
      if(!own&&!task.parent_id&&!['pending','approved','joined'].includes(app?.status))buttons+=cmd('apply','申请接单');
      if(app?.status==='pending')buttons+=cmd('withdraw','撤回申请');
      if(app?.status==='invited')buttons+=cmd('accept_invite','接受任务邀请')+cmd('decline_invite','拒绝任务邀请');
      if(own&&task.status==='offered')buttons+=cmd('accept_subtask','接受负责子任务')+cmd('decline_subtask','拒绝负责子任务');
      if(own&&task.status!=='offered')buttons+=cmd('subtask','创建子任务')+cmd('invite',task.parent_id?'邀请父群成员':'邀请在线用户')+cmd('summary-retry','重新生成摘要')+cmd('cancel','取消任务');
      if(task.is_member&&task.status!=='offered')buttons+=cmd('submit','提交结果');
      if(own&&!task.parent_id)buttons+=cmd('finish','结束任务');
      if(!own&&task.is_member)buttons+=cmd('leave','退出任务');
    }
    const applications=own?Object.entries(task.applications||{}).map(([node,a])=>`<div class="notice-row"><strong>${esc(host.peerName(node))}</strong><p>${labels[a.status]||esc(a.status)} · ${esc(a.proposal||'')}</p>${a.status==='pending'?cmd('approve','批准加入',`data-node="${esc(node)}"`)+cmd('reject','拒绝',`data-node="${esc(node)}"`):a.status==='joined'?cmd('remove','移出成员',`data-node="${esc(node)}"`):''}</div>`).join(''):'';
    const s=task.summary;
    host.setContent(`<section class="page-section toy-page"><div class="page-actions">${buttons}</div>${app?`<p class="badge">${labels[app.status]||esc(app.status)}</p>`:''}<h3>任务概览</h3>${summary(task)}${s?.status==='ready'?`<h4>交付物</h4>${list(s.content.deliverables)}<h4>约束</h4>${list(s.content.constraints)}<h4>待澄清</h4>${list(s.content.questions)}`:''}<details><summary>查看原始任务</summary><p class="task-original">${esc(task.original)}</p></details><h4>验收要求</h4><p>${esc(task.acceptance)}</p><h3>项目层级</h3><p>第 ${task.depth} 层 / 最多 ${task.max_depth} 层</p>${task.parent_id?cmd('detail','上级任务',`data-id="${esc(task.parent_id)}"`):''}${children.map(c=>`<div class="task-child">${cmd('detail',esc(c.title),`data-id="${esc(c.task_id)}"`)}<span>${labels[c.status]||esc(c.status)}</span></div>`).join('')}${applications?'<h3>申请与成员</h3>'+applications:''}<h3>提交与验收</h3>${(task.submissions||[]).map(r=>`<article class="notice-row"><p>${esc(r.text)}</p><p>${esc(r.reference||'')}</p><span class="badge">${esc(r.status)}</span>${r.status==='pending'&&((!task.parent_id&&own)||task.creator_id===me())?cmd('review-accept','验收通过',`data-submission="${esc(r.submission_id)}"`)+cmd('review-reject','退回修改',`data-submission="${esc(r.submission_id)}"`):''}</article>`).join('')||'<p>暂无提交</p>'}</section>`,true);
  }
  function taskForm(parent){
    const members=parent?(host.projects().find(p=>p.project_id===parent.project_id)?.project_members||[]):[];
    host.modal(parent?'创建子任务':'发布任务',`<form id="toy-task-form"><label>标题<input name="title" maxlength="200" required></label><label>任务原文<textarea name="original" rows="6" maxlength="20000" required></textarea></label><label>验收要求<textarea name="acceptance" rows="2" maxlength="4000" required></textarea></label>${parent?`<label>子任务负责人<select name="owner_id">${members.map(m=>`<option value="${esc(m.agent_id)}">${esc(host.peerName(m.agent_id))}</option>`).join('')}</select></label>`:'<label>最大层数<input name="max_depth" type="number" min="1" max="8" value="3"></label>'}<p>${parent?'负责人接受后创建独立子群，成员只从父群邀请。':'任务原文会公开广播，发布后由本机 Harness 自动生成摘要。请勿填写秘密。'}</p><button class="primary" type="submit">${parent?'发送子任务邀请':'发布'}</button><p id="toy-form-error" role="alert"></p></form>`);
    const requestId=crypto.randomUUID();
    $('#toy-task-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget;const data=Object.fromEntries(new FormData(form));if(data.max_depth)data.max_depth=Number(data.max_depth);data.request_id=requestId;form.querySelector('button').disabled=true;try{const result=await host.api(parent?`/v1/tasks/${encodeURIComponent(parent.task_id)}/subtasks`:'/v1/tasks',data);if(result.status==='queued')throw Error('已排队，可使用相同表单重试');$('#dialog').close();await refresh();show('task:'+result.task_id);}catch(err){$('#toy-form-error').textContent=err.message;form.querySelector('button').disabled=false;}};
  }
  async function run(fn){try{await fn();}catch(err){host.toast(err.message);}}
  async function action(name,data={}){
    const task=tasks.find(t=>'task:'+t.task_id===selected);if(!task)return;
    await host.api(`/v1/tasks/${encodeURIComponent(task.task_id)}/actions`,{action:name,request_id:crypto.randomUUID(),...data});await refresh();render(selected);
  }
  function confirm(title, description, fn){host.modal(title,`<p>${esc(description)}</p><button class="primary" id="toy-confirm">确认</button>`);$('#toy-confirm').onclick=()=>run(async()=>{await fn();$('#dialog').close();});}
  function click(event){const button=event.target.closest('[data-toy]');if(!button)return;
    const command=button.dataset.toy,task=tasks.find(t=>'task:'+t.task_id===selected);
    if(command==='hall')return show('tasks');if(command==='profile')return show('toy-profile');
    if(command==='detail'){run(async()=>{host.api('/v1/toy/trace',{kind:'ui.view',object_id:button.dataset.id,payload:{screen:'task-detail'}}).catch(()=>{});show('task:'+button.dataset.id);});return;}
    if(command==='publish')return taskForm();if(command==='subtask')return taskForm(task);
    if(command==='group')return host.openProject(button.dataset.project);
    if(command==='withdraw-consent')return confirm('退出采集','停止新增上传，历史数据由管理员保留或删除。',async()=>{await host.api('/v1/toy/profile',{nickname:profile.profile.nickname,consent_active:false});await refresh();renderConsent();});
    if(!task)return;
    if(command==='apply')return confirm('申请接单',`申请加入「${task.title}」及项目群。批准后启用群内临时信任，并采集群聊内容；不会升级全局信任。`,()=>action('apply',{terms_hash:task.terms_hash,proposal:'愿意参加该任务'}));
    if(['accept_invite','accept_subtask'].includes(command))return confirm('确认参与','同意此任务、群成员权限与实验采集；不自动加入其他群。',()=>action(command,{terms_hash:task.terms_hash}));
    if(command==='invite'){
      return run(async()=>{
      const parent=tasks.find(t=>t.task_id===task.parent_id);const members=task.parent_id?(host.projects().find(p=>p.project_id===parent?.project_id)?.project_members||[]):((await host.api('/v1/peers/candidates')).peers||[]).filter(p=>p.online===true);
      const candidates=members.filter(m=>m.agent_id!==me()&&!task.member_ids?.includes(m.agent_id));
      host.modal(task.parent_id?'邀请父群成员':'邀请在线用户',`<select id="toy-invite-node">${candidates.map(m=>`<option value="${esc(m.agent_id)}">${esc(host.peerName(m.agent_id))}</option>`).join('')}</select>${candidates.length?'':'<p>暂无可邀请的在线用户</p>'}<button class="primary" id="toy-invite-confirm" ${candidates.length?'':'disabled'}>发送邀请</button>`);
      $('#toy-invite-confirm').onclick=()=>run(async()=>{await action('invite',{node_id:$('#toy-invite-node').value});$('#dialog').close();});});
    }
    if(command==='submit'){
      host.modal('提交结果','<form id="toy-result"><label>结果说明<textarea name="text" rows="5" required></textarea></label><label>引用（可选）<input name="reference"></label><button class="primary">提交</button></form>');
      $('#toy-result').onsubmit=e=>{e.preventDefault();run(async()=>{await action('submit',Object.fromEntries(new FormData(e.currentTarget)));$('#dialog').close();});};return;
    }
    if(command==='summary-retry')return run(async()=>{await host.api(`/v1/tasks/${encodeURIComponent(task.task_id)}/summary-retry`,{});host.toast('摘要已排队');});
    if(command.startsWith('review-'))return confirm('验收结果','仅确认当前提交，系统不会自动验证交付物质量。',()=>action('review',{submission_id:button.dataset.submission,decision:command.slice(7),reason:command.endsWith('accept')?'验收通过':'需要修改'}));
    confirm('确认操作',`${button.textContent.trim()}：${task.title}`,()=>action(command,button.dataset.node?{node_id:button.dataset.node}:{}));
  }
  return {init,refresh,render,show,enabled:()=>profile.mode==='toy',tasks:()=>tasks,profile:()=>profile,nickname:id=>people.find(p=>p.node_id===id)?.nickname};
})();
