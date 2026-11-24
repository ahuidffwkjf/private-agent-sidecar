'use strict';

(() => {
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = () => window.lucide?.createIcons();
  const enc = encodeURIComponent;
  const state = {view:'chats', selected:'public', identity:null, peers:[], projects:[], skills:[], actors:[],
    categories:[], notifications:[], invitations:[], drafts:new Map(), previews:new Map(), filter:'', installedOnly:false,
    revision:0, detailRead:0, refreshBusy:false, sending:false, lastContent:'', catalogLoaded:false};
  const titles = {chats:'聊天', contacts:'通讯录', discover:'发现', skills:'Skills', me:'我'};
  const toy=window.SidecarToy;
  let desktopToyStarted=false;
  toy?.init({api,esc,icon,heading,setContent,modal,toast,peerName:id=>peerName(id),projects:()=>state.projects,peers:()=>state.peers,
    selectTask:id=>{saveDraft();state.view='discover';state.selected=id;state.revision++;$('#view-title').textContent='发现';document.querySelectorAll('.nav-button[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view==='discover'));$('#app').classList.add('detail-open');renderList();renderDetail(true);},
    openProject:id=>{chooseView('chats');select('project:'+id);}});
  const relations = {pending:'待添加', trusting:'等待对方确认', trusted_by_peer:'对方已信任你', mutual:'双向信任',
    revoked:'已撤销信任', blocked:'已阻止', rejected_by_peer:'对方已拒绝', revoked_by_peer:'对方已撤销', blocked_by_peer:'被对方阻止'};

  async function api(path, data, method='POST') {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {signal:controller.signal, ...(data === undefined ? {} :
        {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || `请求失败 (${response.status})`);
      return value;
    } finally { clearTimeout(timer); }
  }
  function error(message='') { $('#error').hidden=!message; $('#error').textContent=message; }
  function toast(message) {
    $('#toast').textContent=message; $('#toast').hidden=false;
    clearTimeout(toast.timer); toast.timer=setTimeout(() => { $('#toast').hidden=true; }, 4500);
  }
  const short = id => id ? id.replace(/^agent:/, '').slice(0,10) : '未知节点';
  const peerName = id => toy?.nickname(id) || state.peers.find(p => p.agent_id===id)?.display_name || `节点 ${short(id)}`;
  const stamp = seconds => seconds ? new Date(seconds*1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
  function avatar(kind, title='S', own=false) {
    if (kind==='public') return `<span class="avatar green">${icon('radio')}</span>`;
    if (kind==='project') return `<span class="avatar blue">${icon('users-round')}</span>`;
    return `<span class="avatar ${own?'green':'rose'}">${esc(title.slice(0,2))}</span>`;
  }
  const empty = (title, text='', symbol='messages-square') => `<div class="empty">${icon(symbol)}<h3>${esc(title)}</h3>${text?`<p>${esc(text)}</p>`:''}</div>`;
  const bodyText = value => typeof value==='string' ? value : value?.text ?? JSON.stringify(value ?? {}, null, 2);
  function setContent(html, force=false) {
    if (!force && html===state.lastContent) return;
    const content=$('#content');
    const nearBottom=content.scrollHeight-content.scrollTop-content.clientHeight<100;
    const previous=content.scrollTop;
    content.innerHTML=html; state.lastContent=html; icons();
    content.scrollTop=(state.view==='chats' && (nearBottom || force)) ? content.scrollHeight : force ? 0 : previous;
  }
  function heading(title, subtitle) { $('#detail-title').textContent=title; $('#detail-subtitle').textContent=subtitle; }
  function row(id, title, subtitle, glyph, color='rose', count='') {
    return `<button class="row ${state.selected===id?'selected':''}" data-select="${esc(id)}" aria-current="${state.selected===id?'true':'false'}"><span class="avatar ${color}">${icon(glyph)}</span><span class="row-text"><span class="row-title">${esc(title)}</span><span class="row-subtitle">${esc(subtitle)}</span></span>${count?`<span class="count">${esc(count)}</span>`:''}</button>`;
  }
  function conversations() {
    return [{id:'public',name:'公共广场',kind:'public',subtitle:'公开广播 · Relay'},
      ...state.projects.filter(p => p.status==='active').map(p => {const task=toy?.tasks().find(t=>t.project_id===p.project_id);return {id:`project:${p.project_id}`,name:p.name,kind:'project',subtitle:task?`${task.parent_id?'子任务群':'主任务群'} · 第 ${task.depth} 层`:'项目讨论 · 成员直连',project:p};}),
      ...state.peers.filter(p => p.relationship_state==='mutual').map(p => ({id:`private:${p.agent_id}`,name:peerName(p.agent_id),kind:'private',subtitle:p.direct?.endpoint?'双向信任 · 直连私聊':'未配置直连地址',peer:p}))];
  }
  function currentConversation() { return conversations().find(c => c.id===state.selected); }
  function filteredSkills() {
    const q=$('#search').value.trim().toLowerCase();
    return state.skills.filter(s => (!state.filter || s.category===state.filter) && (!state.installedOnly || s.installed) &&
      [s.name,s.skill_id,s.description,s.category].join(' ').toLowerCase().includes(q));
  }
  function renderList() {
    const q=$('#search').value.trim().toLowerCase();
    const match=(...values) => values.join(' ').toLowerCase().includes(q);
    let html='';
    if (state.view==='chats') {
      html=conversations().filter(c => match(c.name,c.id)).map(c => row(c.id,c.name,state.previews.get(c.id)||c.subtitle,c.kind==='public'?'radio':c.kind==='project'?'users-round':'user-round',c.kind==='public'?'green':c.kind==='project'?'blue':'rose')).join('');
    } else if (state.view==='contacts') {
      html=row('requests','新的联系人','信任申请与项目邀请','user-round-plus','amber',String(state.notifications.filter(n=>n.decision==='trust'&&!n.outgoing_trust).length+state.invitations.filter(i=>i.status==='pending').length));
      html+='<div class="section-label">节点联系人</div>';
      html+=state.peers.filter(p=>match(p.display_name,p.agent_id)).sort((a,b)=>Number(b.online===true)-Number(a.online===true)).map(p=>row(p.agent_id,peerName(p.agent_id),(p.online===undefined?'':p.online?'在线 · ':'离线 · ')+(relations[p.relationship_state]||p.relationship_state),'user-round')).join('');
      if (!state.peers.length) html+='<p class="empty-small">尚未发现其他节点</p>';
    } else if (state.view==='skills') {
      const skills=filteredSkills();
      html=`<div class="section-label">${skills.length} 项 · 已安装 ${state.skills.filter(s=>s.installed).length}</div>`;
      html+=skills.map(s=>`<button class="row skill-row ${state.selected===s.skill_id?'selected':''}" data-select="${esc(s.skill_id)}"><span class="avatar ${s.installed?'green':'rose'}">${icon(s.installed?'check':'puzzle')}</span><span class="row-text"><span class="row-title">${esc(s.name)}</span><span class="row-subtitle">${esc(s.category)}</span></span></button>`).join('');
      if (!skills.length) html+='<p class="empty-small">没有匹配的 Skill</p>';
    } else if (state.view==='discover') {
      html=[['tasks','广播大厅','协作任务与项目群','clipboard-list','blue'],['feed','公共动态','公开广播信息流','rss','green']]
        .filter(r=>match(r[1],r[2])).map(r=>row(...r)).join('');
    } else {
      html=[['profile','我的节点','身份与直连状态','user-round','green'],['actors','本机智能体',`${state.actors.length} 个智能体`,'bot','blue'],['settings','设置','Harness、信任与运行记录','settings-2','blue']]
        .filter(r=>match(r[1],r[2])).map(r=>row(...r)).join('');
    }
    const list=$('#list');
    // Avoid replacing focused controls during unchanged background polling.
    if (list.innerHTML!==html) list.innerHTML=html||'<p class="empty-small">没有匹配项</p>';
    icons();
  }
  function saveDraft() {
    if (state.view==='chats') state.drafts.set(state.selected,$('#message').value);
  }
  function chooseView(view) {
    saveDraft(); state.view=view; state.revision++; error();
    state.selected=({chats:'public',contacts:'requests',discover:'feed',skills:state.skills[0]?.skill_id||'',me:'profile'})[view];
    $('#search').value=''; $('#view-title').textContent=titles[view]; document.title=`Sidecar · ${titles[view]}`;
    $('#list').setAttribute('aria-label',view==='chats'?'会话列表':`${titles[view]}列表`);
    document.querySelectorAll('.nav-button[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
    $('#filters').innerHTML=view==='skills'?`<select class="filter-select" id="category" aria-label="Skill 分类"><option value="">全部分类</option>${state.categories.map(c=>`<option value="${esc(c.name)}">${esc(c.name)} (${c.count})</option>`).join('')}</select><select class="filter-select" id="installed" aria-label="Skill 安装状态"><option value="all">全部 Skills</option><option value="installed">已安装</option></select>`:'';
    state.filter=''; state.installedOnly=false;
    $('#app').classList.remove('detail-open'); $('#message').value=state.drafts.get(state.selected)||'';
    $('#send-status').textContent=''; $('#emoji-picker').hidden=true;
    renderList(); renderDetail(true);
    if (view==='skills' && !state.catalogLoaded) loadCatalog();
  }
  function select(id) {
    saveDraft(); state.selected=id; state.revision++; error();
    $('#message').value=state.drafts.get(id)||''; $('#send-status').textContent='';
    $('#app').classList.add('detail-open'); $('#emoji-picker').hidden=true;
    renderList(); renderDetail(true);
    if(toy?.profile().research?.consent_active)api('/v1/toy/trace',{kind:'ui.view',object_id:id,payload:{screen:state.view}}).catch(e=>error('浏览记录暂未保存：'+e.message));
  }
  function dl(items) { return `<dl class="data-list">${items.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(Array.isArray(v)?v.join('、'):v)}</dd></div>`).join('')}</dl>`; }
  async function renderDetail(force=false) {
    const revision=state.revision;
    const read=++state.detailRead;
    $('#composer').hidden=true; $('#detail-action').hidden=false;
    if (state.view==='chats') {
      const conversation=currentConversation();
      if (!conversation) { heading('会话不可用','成员资格或信任状态已改变'); setContent(empty('请选择一个会话'),force); return; }
      heading(conversation.name,conversation.subtitle);
      const canSend=conversation.kind!=='private'||!!conversation.peer.direct?.endpoint;
      $('#composer').hidden=false; $('#send').disabled=state.sending||!canSend; $('#message').disabled=!canSend;
      $('#compose-route').textContent=conversation.subtitle;
      if (force) setContent(empty('正在读取消息'),true);
      try {
        let messages=[];
        if (conversation.kind==='public') {
          const data=await api('/v1/broadcasts');
          messages=(data.broadcasts||[]).filter(b=>b.channel==='public').map(b=>({id:b.broadcast_id,author:b.sender_id,time:b.created_at,text:bodyText(b.content)}));
        } else if (conversation.kind==='project') {
          const data=await api(`/v1/projects/${enc(conversation.project.project_id)}/board`);
          messages=(data.events||[]).map(m=>({id:m.event_id,author:m.author_agent_id,time:m.created_at,text:bodyText(m.content)}));
        } else {
          const data=await api(`/v1/private-messages?peer_id=${enc(conversation.peer.agent_id)}&limit=200`);
          messages=(data.messages||[]).map(m=>({id:m.message_id,author:m.direction==='outgoing'?state.identity?.agent_id:conversation.peer.agent_id,time:m.created_at,text:bodyText(m.payload),status:m.status}));
        }
        if (revision!==state.revision || read!==state.detailRead) return;
        messages.sort((a,b)=>a.time-b.time);
        if (messages.length) state.previews.set(conversation.id,messages.at(-1).text.slice(0,50));
        setContent(messages.length?'<div class="timeline-label">最近消息</div>'+messages.map(m=>{
          const own=m.author===state.identity?.agent_id;
          const delivery=own?({queued:'等待投递',blocked:'投递已阻止',expired:'消息已过期',cancelled:'信任已撤销',delivered:'已投递'}[m.status]||''):'';
          return `<article class="message-row ${own?'own':''}" data-message-id="${esc(m.id)}">${avatar('private',own?'我':short(m.author),own)}<div class="message-body"><div class="message-meta"><span>${esc(own?'我':peerName(m.author))}</span><time>${esc(stamp(m.time))}</time><span>${esc(delivery)}</span></div><div class="bubble">${esc(m.text)}</div></div></article>`;
        }).join(''):empty('还没有消息',conversation.kind==='public'?'公共广场的消息对 Relay 上的节点公开。':'开始这段讨论。'),force);
        renderList();
      } catch (e) {
        if (revision===state.revision && read===state.detailRead) { error(`消息读取失败：${e.message}`); if(force)setContent(empty('暂时无法读取消息','可使用左下方刷新重试。','wifi-off'),true); }
      }
    } else if (state.view==='contacts') {
      if (state.selected==='requests') { renderRequests(force); return; }
      const peer=state.peers.find(p=>p.agent_id===state.selected);
      if (!peer) { heading('通讯录','节点联系人'); setContent(empty('请选择联系人'),force); return; }
      heading('联系人资料',relations[peer.relationship_state]||peer.relationship_state);
      setContent(`<section class="page-section"><div class="profile-block">${avatar('private',short(peer.agent_id))}<div><h3>${esc(peerName(peer.agent_id))}</h3><p>${esc(peer.agent_id)}</p></div></div>${dl([['关系',relations[peer.relationship_state]||peer.relationship_state],['在线状态',peer.online===undefined?'未探测':peer.online?'在线':'离线'],['签名指纹',peer.key_fingerprint||'未提供'],['通信路线',peer.direct?.transport==='relay-ciphertext'?'ECS 密文转发':peer.direct?.endpoint||'未配置'],['智能体',(peer.actors||[]).map(a=>`${a.name}\n${a.actor_id}`).join('\n\n')||'暂无公开身份']])}<div class="page-actions">${peer.relationship_state==='mutual'?'<button class="primary" data-command="chat">发消息</button><button class="secondary" data-command="actor-send">发给智能体</button>':!peer.outgoing_trust?'<button class="primary" data-command="trust">添加联系人</button>':'<span class="badge">等待对方确认</span>'}${peer.online?'<button class="secondary" data-command="task-invite">邀请参加任务</button>':''}${peer.outgoing_trust?'<button class="secondary danger" data-command="revoke">撤销信任</button>':''}</div></section>`,force);
    } else if (state.view==='skills') {
      $('#detail-action').hidden=true;
      const skill=state.skills.find(s=>s.skill_id===state.selected);
      if (!skill) { heading('Skills',`${state.skills.length} 项功能契约`); setContent(empty('选择一个 Skill 查看详情'),force); return; }
      heading(skill.name,skill.category);
      setContent(`<section class="page-section"><div class="profile-block"><span class="avatar blue">${icon('puzzle')}</span><div><h3>${esc(skill.name)}</h3><p>${esc(skill.skill_id)}</p></div></div><span class="badge ${skill.installed?'ready':''}">${skill.installed?'已安装 · 原生通信工具':'未安装'}</span><p class="notice-line">${esc(skill.description)}</p>${dl([['操作入口',skill.operation_entry],['动作',skill.actions],['使用角色',skill.roles],['前置条件',skill.prerequisites],['权限',skill.visibility_permissions],['状态',skill.states_results],['限制',skill.limitations_exceptions],['平台',skill.platforms]])}<div class="page-actions"><button class="primary" data-command="install" ${skill.installed?'disabled':''}>${skill.installed?'已安装':'安装 Skill'}</button><button class="secondary" data-command="run-skill" ${skill.installed?'':'disabled'}>运行 Skill</button><button class="secondary" data-command="manifest">查看 Manifest</button></div></section>`,force);
    } else if (state.view==='discover') {
      $('#detail-action').hidden=true;
      if(state.selected==='tasks'||state.selected==='toy-profile'||state.selected.startsWith('task:')){toy?.render(state.selected,force);return;}
      if (state.selected==='feed') {
        heading('公共动态','公开广播');
        if (force) setContent(empty('正在读取动态'),true);
        try {
          const data=await api('/v1/broadcasts');
          if (revision!==state.revision || read!==state.detailRead) return;
          const posts=(data.broadcasts||[]).filter(b=>b.channel==='public').sort((a,b)=>b.created_at-a.created_at);
          setContent(`<section class="page-section"><div class="page-actions"><button class="primary" data-command="public-chat">发布公开消息</button></div>${posts.length?posts.map(p=>`<article class="feed-post">${avatar('private',short(p.sender_id))}<div><div class="feed-author">${esc(peerName(p.sender_id))}</div><p class="feed-body">${esc(bodyText(p.content))}</p><time class="feed-time">${esc(stamp(p.created_at))}</time></div></article>`).join(''):empty('还没有公开动态','','rss')}</section>`,force);
        } catch (e) { if(revision===state.revision && read===state.detailRead){error(e.message);if(force)setContent(empty('动态读取失败'),true);} }
      }
    } else {
      $('#detail-action').hidden=true;
      heading(state.selected==='settings'?'设置':'我的节点','Sidecar');
      if (state.selected==='actors') {
        heading('本机智能体',`${state.actors.length} 个 · Harness`);
        setContent(`<section class="page-section"><div class="page-actions"><button class="primary" data-command="actor-register">${icon('plus')}注册智能体</button></div>${state.actors.map(a=>`<article class="notice-row"><h3>${esc(a.name)}</h3><p>${esc(a.role)}</p>${dl([['智能体 ID',a.actor_id],['Harness ID',a.harness_id],['所属节点',a.node_agent_id]])}<button class="secondary" data-actor-inbox="${esc(a.actor_id)}">查看收件箱</button></article>`).join('')||empty('暂无智能体','','bot')}</section>`,force);
      } else if (state.selected==='settings') {
        setContent(`<section class="page-section"><div class="page-links"><a class="page-link" href="/ui/admin">${icon('settings-2')}<span>高级管理<small> · Harness 与运行记录</small></span>${icon('chevron-right')}</a><a class="page-link" href="/ui/trust">${icon('shield-check')}<span>信任管理</span>${icon('chevron-right')}</a><button class="page-link" data-command="skills">${icon('blocks')}<span>Skill 管理</span>${icon('chevron-right')}</button></div></section>`,force);
      } else {
        setContent(`<section class="page-section"><div class="profile-block">${avatar('private','S',true)}<div><h3>我的 Sidecar</h3><p>${esc(state.identity?.agent_id||'身份未加载')}</p></div></div>${dl([['节点标识',state.identity?.agent_id||'未加载'],['联系人',`${state.peers.length} 个节点`],['双向信任',`${state.peers.filter(p=>p.relationship_state==='mutual').length} 个节点`],['项目讨论',`${state.projects.filter(p=>p.status==='active').length} 个`],['Skills',`${state.skills.filter(s=>s.installed).length} / ${state.skills.length} 已安装`]])}<div class="page-links"><button class="page-link" data-command="settings">${icon('settings-2')}<span>设置</span>${icon('chevron-right')}</button></div></section>`,force);
      }
    }
  }
  function renderRequests(force) {
    heading('新的联系人','信任申请与项目邀请');
    const requests=state.notifications.filter(n=>n.decision==='trust'&&!n.outgoing_trust);
    const invites=state.invitations.filter(i=>i.status==='pending');
    const html=requests.map(n=>`<article class="notice-row"><h3>${esc(peerName(n.issuer_agent_id))}</h3><p>对方请求建立双向信任</p><p>${esc(n.issuer_agent_id)}</p><div class="page-actions"><button class="primary" data-trust-response="accept" data-peer="${esc(n.issuer_agent_id)}">接受并信任</button><button class="secondary" data-trust-response="reject" data-peer="${esc(n.issuer_agent_id)}">拒绝</button></div></article>`).join('')+invites.map(i=>`<article class="notice-row"><h3>${esc(i.project_name)}</h3><p>项目邀请 · ${esc(i.owner_agent_id)}</p><div class="page-actions"><button class="primary" data-invite-response="accept" data-invite="${esc(i.invitation_id)}">接受并加入</button><button class="secondary" data-invite-response="reject" data-invite="${esc(i.invitation_id)}">拒绝</button></div></article>`).join('');
    const taskInvites=(toy?.tasks()||[]).filter(t=>t.applications?.[state.identity?.agent_id]?.status==='invited');
    const allHtml=html+taskInvites.map(t=>`<article class="notice-row"><h3>${esc(t.title)}</h3><p>任务邀请 · ${esc(peerName(t.owner_id))}</p><button class="primary" data-open-task="${esc(t.task_id)}">查看邀请与权限</button></article>`).join('');
    setContent(allHtml?`<section class="page-section">${allHtml}</section>`:empty('暂无新申请','','user-round-check'),force);
  }
  function modal(title, html) {
    $('#dialog-title').textContent=title; $('#dialog-body').innerHTML=html; icons();
    if (!$('#dialog').open) $('#dialog').showModal();
  }
  function confirmAction(title, description, action) {
    modal(title,`<p>${esc(description)}</p><div class="page-actions"><button class="secondary" id="cancel-dialog">取消</button><button class="primary" id="confirm-dialog">确认</button></div><p id="dialog-error" role="alert"></p>`);
    $('#cancel-dialog').onclick=()=>$('#dialog').close();
    $('#confirm-dialog').onclick=async()=>{
      $('#confirm-dialog').disabled=true;
      try { await action(); $('#dialog').close(); await refresh(); await renderDetail(); }
      catch(e) { $('#dialog-error').textContent=e.message; $('#confirm-dialog').disabled=false; }
    };
  }
  async function loadCatalog() {
    try {
      const catalog=await api('/v1/skills/catalog'); state.skills=catalog.skills||[]; state.categories=catalog.categories||[];state.catalogLoaded=true;
      if(state.view==='skills') {
        if(!state.selected)state.selected=state.skills[0]?.skill_id||'';
        const oldFilter=state.filter;
        $('#category').innerHTML='<option value="">全部分类</option>'+state.categories.map(c=>`<option value="${esc(c.name)}">${esc(c.name)} (${c.count})</option>`).join('');
        $('#category').value=oldFilter;renderList();renderDetail();
      }
    } catch(e) { error('Skill 目录读取失败：'+e.message); }
  }
  async function refresh() {
    if(state.refreshBusy)return;
    state.refreshBusy=true;
    try {
      const results=await Promise.allSettled([api('/v1/identity'), api('/v1/peers/candidates'), api('/v1/projects'),api('/v1/peers/trust-notifications'),api('/v1/project-invitations'),api('/v1/actors')]);
      const [identity,peers,projects,notifications,invitations,actors]=results;
      if(actors.status==='fulfilled')state.actors=actors.value.actors||[];
      if(identity.status==='fulfilled')state.identity=identity.value;
      if(peers.status==='fulfilled')state.peers=peers.value.peers||[];
      if(projects.status==='fulfilled')state.projects=projects.value.projects||[];
      if(notifications.status==='fulfilled')state.notifications=notifications.value.notifications||[];
      if(invitations.status==='fulfilled')state.invitations=invitations.value.incoming||[];
      try { await toy?.refresh(); } catch(e) { error('任务同步失败：'+e.message); }
      if(!desktopToyStarted&&new URLSearchParams(location.search).get('toy')==='1'&&toy?.enabled()){
        desktopToyStarted=true;chooseView('discover');select('tasks');
      }
      const offline=results.some(r=>r.status==='rejected');
      $('.connection').classList.toggle('online',!offline);$('#connection-label').textContent=offline?'部分服务不可用':'Sidecar 已连接';
      if(new URLSearchParams(location.search).get('online')==='1'){
        try{const network=await api('/v1/online/status');const connected=!offline&&network.last_connected&&Date.now()/1000-network.last_connected<90;$('.connection').classList.toggle('online',!!connected);$('#connection-label').textContent=connected?`ECS 已连接${network.pending_messages?' · '+network.pending_messages+' 条待投递':''}${network.failed_messages?' · '+network.failed_messages+' 条未送达':''}`:'ECS 未连接 · 正在重试';}catch{$('#connection-label').textContent='ECS 状态不可用';}
      }
      if(offline)error('部分服务暂时不可用，当前保留最近一次数据。');
      renderList();
      // Message reads remain available even when peer discovery through Relay fails.
      if(state.view==='chats'||state.view==='contacts'||state.view==='discover')await renderDetail();
      else if(state.view==='me')renderDetail();
    } finally { state.refreshBusy=false; }
  }
  async function send(event) {
    event.preventDefault();
    const conversation=currentConversation(), text=$('#message').value.trim();
    if(!conversation||!text||state.sending)return;
    const revision=state.revision, draft=$('#message').value;
    state.sending=true;$('#send').disabled=true;$('#send-status').textContent='发送中…';
    try {
      let result;
      if(conversation.kind==='project') result=await api(`/v1/projects/${enc(conversation.project.project_id)}/board`,{event_type:'post',content:{text}});
      else result=await api('/v1/messages/send',{target:conversation.kind==='public'?{kind:'public',channel:'public'}:{kind:'private',recipient_id:conversation.peer.agent_id},message:{type:'chat.message',payload:{text}},ttl_seconds:86400});
      const failed=(result.deliveries||[]).filter(d=>d.status==='failed');
      if(failed.length)throw new Error(failed[0].error||'直连投递失败');
      const pending=(result.sync?.deliveries||[]).some(d=>d.status==='failed');
      const queued=[...(result.deliveries||[]),...(result.sync?.deliveries||[])].some(d=>d.status==='queued');
      if(state.drafts.get(conversation.id)===draft)state.drafts.delete(conversation.id);
      if(revision===state.revision) {
        if($('#message').value===draft)$('#message').value='';
        $('#send-status').textContent=queued?'已进入持久队列，等待投递':pending?'已保存到本地，部分成员待同步':'已发送';
        await renderDetail();
      } else toast(queued?'消息已进入持久队列':pending?'消息已保存，部分成员待同步':'消息已发送');
    } catch(e) { if(revision===state.revision)$('#send-status').textContent='发送失败：'+e.message;else toast('发送失败，原会话草稿已保留'); }
    finally { state.sending=false;$('#send').disabled=state.view!=='chats'||(currentConversation()?.kind==='private'&&!currentConversation()?.peer.direct?.endpoint); }
  }
  function createProject() {
    modal('创建项目讨论',`<form id="project-form"><label for="project-name">讨论名称</label><input id="project-name" type="text" maxlength="100" required placeholder="输入项目名称"><p>向所选节点发送项目邀请。对方接受后才会加入；项目成员信任不会升级为全局信任。</p><div class="choices">${state.peers.map(p=>`<label><input type="checkbox" name="member" value="${esc(p.agent_id)}"><span>${esc(peerName(p.agent_id))}<small class="muted"> · ${esc(relations[p.relationship_state]||p.relationship_state)}</small></span></label>`).join('')||'<p>暂无其他节点，可先创建讨论。</p>'}</div><div class="page-actions"><button type="submit" class="primary">创建讨论</button></div><p id="project-error" role="alert"></p></form>`);
    $('#project-form').onsubmit=async event=>{
      event.preventDefault(); const name=$('#project-name').value.trim();if(!name)return;
      const button=event.currentTarget.querySelector('button[type=submit]');button.disabled=true;
      const members=[...event.currentTarget.querySelectorAll('input[name=member]:checked')].map(e=>e.value);
      try {
        const project=await api('/v1/projects',{name});
        const results=await Promise.allSettled(members.map(id=>api(`/v1/projects/${enc(project.project_id)}/invitations`,{agent_id:id})));
        const failed=results.filter(r=>r.status==='rejected').length;
        $('#dialog').close(); await refresh();chooseView('chats');select(`project:${project.project_id}`);
        toast(failed?`讨论已创建；${failed} 个邀请未送达，可在讨论详情重试。`:'讨论已创建'+(members.length?'，已发送邀请':''));
      } catch(e) { $('#project-error').textContent=e.message;button.disabled=false; }
    };
  }
  function conversationDetails() {
    const c=currentConversation();
    if(state.view==='contacts'){modal('关于节点信任','<p>需要双方分别确认信任后才能私聊。请通过独立渠道核对对方签名公钥。</p>');return;}
    if(!c)return;
    if(c.kind==='project') {
      const p=c.project;
      const task=toy?.tasks().find(t=>t.project_id===p.project_id);
      if(task){toy.show('task:'+task.task_id);return;}
      const own=p.owner_id===state.identity?.agent_id;
      modal('讨论详情',`${dl([['名称',p.name],['项目 ID',p.project_id],['Owner',p.owner_id],['当前成员',(p.project_members||[]).map(m=>peerName(m.agent_id))],['状态',p.status]])}<div class="page-actions"><button class="secondary" id="sync-board">同步消息</button>${own?'<button class="secondary" id="invite-member">邀请成员</button>':''}<a class="secondary" href="/ui/admin">管理项目</a><button class="secondary danger" id="exit-project">${icon(own?'circle-stop':'log-out')} ${own?'结束并退出项目':'退出项目'}</button></div><p id="details-status" role="status"></p>`);
      $('#sync-board').onclick=async()=>{const button=$('#sync-board');button.disabled=true;try{await api(`/v1/projects/${enc(p.project_id)}/board/sync`,{});$('#details-status').textContent='同步已完成';await renderDetail();}catch(e){$('#details-status').textContent=e.message;}finally{button.disabled=false;}};
      if($('#invite-member'))$('#invite-member').onclick=()=>inviteMember(p);
      $('#exit-project').onclick=()=>exitProject(p);
    } else modal(c.name,dl([['通信路径',c.kind==='public'?'Relay 公开广播':'Sidecar 直连，不回退 Relay'],['可见范围',c.kind==='public'?'Relay 上所有可读取该频道的节点':'会话参与双方'],['地址',c.peer?.direct?.endpoint||'公共频道']]));
  }
  function exitProject(project) {
    const own=project.owner_id===state.identity?.agent_id;
    const title=own?'结束并退出项目':'退出项目';
    const description=own
      ?`结束「${project.name}」并退出讨论？本机将停止项目通信、撤销项目临时信任，并尝试通知其他成员。历史记录会保留，单独建立的好友信任不受影响。`
      :`退出「${project.name}」？此操作只关闭你在本机的项目会话并撤销本机项目临时信任，不会解散其他成员的项目，也不会修改发起人的成员名单。历史记录和单独建立的好友信任会保留。`;
    confirmAction(title,description,async()=>{
      const result=await api(`/v1/projects/${enc(project.project_id)}/close`,{});
      if(result.status!=='closed')throw new Error('项目尚未关闭，请重试。');
      state.projects=state.projects.map(p=>p.project_id===project.project_id?{...p,...result}:p);
      chooseView('chats');
      state.drafts.delete(`project:${project.project_id}`);
      state.previews.delete(`project:${project.project_id}`);
      toast(own?'项目已结束并退出':'已退出本机项目会话');
    });
  }
  function inviteMember(project) {
    const members=new Set((project.project_members||[]).map(m=>m.agent_id));
    const candidates=state.peers.filter(p=>!members.has(p.agent_id)&&p.online!==false);
    modal('邀请成员',`<form id="invite-form"><div class="choices">${candidates.map(p=>`<label><input type="checkbox" name="member" value="${esc(p.agent_id)}"><span>${esc(peerName(p.agent_id))}</span></label>`).join('')||'<p>暂无可邀请的节点</p>'}</div><div class="page-actions"><button class="primary" ${candidates.length?'':'disabled'}>发送邀请</button></div><p id="invite-error" role="alert"></p></form>`);
    $('#invite-form').onsubmit=async e=>{e.preventDefault();const ids=[...e.currentTarget.querySelectorAll('input:checked')].map(x=>x.value);if(!ids.length)return;const button=e.currentTarget.querySelector('button');button.disabled=true;const results=await Promise.allSettled(ids.map(id=>api(`/v1/projects/${enc(project.project_id)}/invitations`,{agent_id:id})));const failed=results.filter(r=>r.status==='rejected');$('#invite-error').textContent=failed.length?`${failed.length} 个邀请失败：${failed[0].reason.message}`:'邀请已发送，等待对方接受';button.disabled=false;};
  }
  async function command(name) {
    const peer=state.peers.find(p=>p.agent_id===state.selected);
    if(name==='task-invite'&&peer){
      const tasks=(toy?.tasks()||[]).filter(t=>t.owner_id===state.identity?.agent_id&&!t.parent_id&&t.status==='open');
      modal('邀请参加任务',`<form id="contact-task-invite"><select name="task">${tasks.map(t=>`<option value="${esc(t.task_id)}">${esc(t.title)}</option>`).join('')}</select>${tasks.length?'':'<p>暂无你发布的开放任务</p>'}<button class="primary" ${tasks.length?'':'disabled'}>发送邀请</button><p role="alert" id="contact-invite-error"></p></form>`);
      $('#contact-task-invite').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;form.querySelector('button').disabled=true;try{await api('/v1/tasks/'+enc(form.elements.task.value)+'/actions',{action:'invite',node_id:peer.agent_id});$('#dialog').close();toast('邀请已发送，等待对方确认');}catch(e){$('#contact-invite-error').textContent=e.message;form.querySelector('button').disabled=false;}};
    }
    if(name==='chat'&&peer){chooseView('chats');select(`private:${peer.agent_id}`);}
    if(name==='public-chat'){chooseView('chats');select('public');$('#message').focus();}
    if(name==='skills')chooseView('skills');
    if(name==='settings')select('settings');
    if(name==='trust'&&peer)confirmAction('添加联系人',`请先独立核对节点 ${peer.agent_id} 的签名公钥：${peer.signing_key}。确认后将授予对方本地长期信任并发送通知；双方确认后才可私聊。`,()=>api('/v1/peers/trust-requests',{agent_id:peer.agent_id,expected_signing_key:peer.signing_key}));
    if(name==='revoke'&&peer)confirmAction('撤销信任',`撤销对 ${peerName(peer.agent_id)} 的长期信任，现有项目内临时信任另行管理。`,()=>api(`/v1/peers/trust/${enc(peer.agent_id)}`,{},'DELETE'));
    const skill=state.skills.find(s=>s.skill_id===state.selected);
    if(name==='actor-register'){
      modal('注册智能体','<form id="actor-form"><label for="actor-name">名称</label><input id="actor-name" type="text" maxlength="100" required><label for="actor-role">职责</label><input id="actor-role" type="text" maxlength="500"><button class="primary">注册</button><p id="actor-error" role="alert"></p></form>');
      $('#actor-form').onsubmit=async e=>{e.preventDefault();const b=e.currentTarget.querySelector('button');b.disabled=true;try{const r=await api('/v1/actors',{name:$('#actor-name').value,role:$('#actor-role').value});$('#dialog').close();await refresh();toast(r.announcement_status==='published'?'智能体已注册':'智能体已注册，能力广播待重试');}catch(e){$('#actor-error').textContent=e.message;b.disabled=false;}};
    }
    if(name==='actor-send'&&peer){
      const options=actors=>actors.map(a=>`<option value="${esc(a.actor_id)}">${esc(a.name)} · ${esc(a.actor_id)}</option>`).join('');
      modal('发送智能体消息',`<form id="actor-send-form"><label for="from-actor">发送智能体</label><select id="from-actor" required>${options(state.actors)}</select><label for="to-actor">接收智能体</label><select id="to-actor" required>${options(peer.actors||[])}</select><label for="actor-text">内容</label><textarea id="actor-text" rows="4" maxlength="20000" required></textarea><div class="page-actions"><button class="primary" ${state.actors.length&&(peer.actors||[]).length?'':'disabled'}>发送</button></div><p id="actor-error" role="alert"></p></form>`);
      $('#actor-send-form').onsubmit=async e=>{e.preventDefault();const b=e.currentTarget.querySelector('button');b.disabled=true;try{const result=await api('/v1/actors/send',{actor_id:$('#from-actor').value,recipient_node_id:peer.agent_id,recipient_actor_id:$('#to-actor').value,text:$('#actor-text').value});$('#dialog').close();toast(result.status==='queued'?'智能体消息已进入持久队列，等待投递':'智能体消息已送达');}catch(e){$('#actor-error').textContent=e.message;b.disabled=false;}};
    }
    if(name==='run-skill'&&skill?.installed){
      const fields=Object.entries(skill.input_schema.properties).map(([key,schema])=>`<label for="arg-${esc(key)}">${esc(schema.description||key)}</label>${schema.enum?`<select id="arg-${esc(key)}" name="${esc(key)}">${schema.enum.map(v=>`<option>${esc(v)}</option>`).join('')}</select>`:`<input type="text" id="arg-${esc(key)}" name="${esc(key)}" ${skill.input_schema.required.includes(key)?'required':''}>`}`).join('');
      modal(skill.name,`<form id="skill-form">${fields}<div class="page-actions"><button class="primary">${skill.mutates?'确认执行':'查询'}</button></div><pre class="code" id="skill-result" hidden></pre></form>`);
      $('#skill-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,b=form.querySelector('button');b.disabled=true;try{const args=Object.fromEntries([...new FormData(form)].filter(([,v])=>v!==''));const r=await api('/mcp',{jsonrpc:'2.0',id:Date.now(),method:'tools/call',params:{name:'skill.'+skill.skill_id,arguments:args}});const result=$('#skill-result');result.hidden=false;result.textContent=JSON.stringify(r.error||r.result,null,2);}catch(e){$('#skill-result').hidden=false;$('#skill-result').textContent=e.message;}finally{b.disabled=false;}};
    }
    if(name==='manifest'&&skill)modal('Skill Manifest',`<pre class="code">${esc(JSON.stringify(skill,null,2))}</pre>`);
    if(name==='install'&&skill){
      const button=$('[data-command="install"]');button.disabled=true;
      try { await api('/v1/skills/catalog/install',{skill_ids:[skill.skill_id]});skill.installed=true;renderList();renderDetail();toast('通信 Skill 已安装'); }
      catch(e){error('安装失败：'+e.message);button.disabled=false;}
    }
  }
  document.addEventListener('click', event=>{
    const target=event.target.closest('button');if(!target)return;
    if(target.dataset.view)chooseView(target.dataset.view);
    if(target.dataset.select)select(target.dataset.select);
    if(target.dataset.command)command(target.dataset.command);
    if(target.dataset.openTask)toy?.show('task:'+target.dataset.openTask);
    if(target.dataset.actorInbox){
      api('/v1/actors/inbox?actor_id='+enc(target.dataset.actorInbox)).then(r=>modal('智能体收件箱',r.messages.map(m=>`<article class="notice-row"><h3>${esc(m.payload.sender_actor.name)}</h3><p>${esc(m.payload.text)}</p><p>${esc(m.sender_node_id)}</p></article>`).join('')||'<p>暂无消息</p>')).catch(e=>error(e.message));
    }
    if(target.dataset.trustResponse){
      const id=target.dataset.peer, decision=target.dataset.trustResponse;
      const peer=state.peers.find(p=>p.agent_id===id);
      confirmAction(decision==='accept'?'接受并信任':'拒绝申请',decision==='accept'?`节点 ${id}；请独立核对签名公钥 ${peer?.signing_key||'未加载，请取消并刷新'}。接受后授予长期信任。`:`拒绝 ${id} 的信任申请。`,()=>{
        if(decision==='accept'&&!peer?.signing_key)throw new Error('未获得签名公钥，请刷新联系人');
        return api('/v1/peers/trust-response',{agent_id:id,decision});
      });
    }
    if(target.dataset.inviteResponse){const id=target.dataset.invite,decision=target.dataset.inviteResponse;confirmAction(decision==='accept'?'加入项目':'拒绝邀请',decision==='accept'?'接受后启用项目成员临时信任，等待 Owner 确认成员清单。':'确认拒绝此项目邀请。',()=>api(`/v1/project-invitations/${enc(id)}/response`,{decision}));}
  });
  $('#search').addEventListener('input',renderList);
  $('#filters').addEventListener('change',e=>{if(e.target.id==='category')state.filter=e.target.value;if(e.target.id==='installed')state.installedOnly=e.target.value==='installed';renderList();});
  $('#back').onclick=()=>$('#app').classList.remove('detail-open');
  $('#refresh').onclick=async()=>{error();await refresh();if(state.view==='skills')await loadCatalog();};
  $('#add').onclick=createProject;
  $('#detail-action').onclick=conversationDetails;
  $('#composer').onsubmit=send;
  $('#message').addEventListener('input',saveDraft);
  $('#message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send(e);}});
  $('#emoji').onclick=()=>{
    const picker=$('#emoji-picker');picker.hidden=!picker.hidden;
    if(!picker.childElementCount){for(const value of ['😀','👍','🎉','❤️','🤝','✅']){const b=document.createElement('button');b.type='button';b.textContent=value;b.setAttribute('aria-label',value);b.onclick=()=>{const input=$('#message');input.setRangeText(value,input.selectionStart,input.selectionEnd,'end');saveDraft();picker.hidden=true;input.focus();};picker.appendChild(b);}}
  };
  $('#dialog').addEventListener('click',e=>{if(e.target===$('#dialog')){const r=$('#dialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#dialog').close();}});
  icons();renderList();refresh();loadCatalog();
  setInterval(()=>{if(!document.hidden)refresh();},6000);
})();
