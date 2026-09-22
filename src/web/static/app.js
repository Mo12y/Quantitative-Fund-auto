// ========== utils ==========
const EMOJI_RE=/[\u{1F300}-\u{1FAFF}\u{1F000}-\u{1F2FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu;
function stripEmoji(s){return String(s==null?'':s).replace(EMOJI_RE,'').replace(/\s+/g,' ').trim();}
function esc(s){return stripEmoji(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function fmt(v,d=1){return (v??0).toFixed(d)}
// 温度分解维度：null 表示该维度数据缺失（已被剔除并重新归一化），不能显示成 0°
function fmtDim(v){return (v==null)?'缺失':fmt(v)}
function fmtPct(v){return ((v??0)>=0?'+':'')+(v??0).toFixed(2)+'%'}
function fmtMoney(v){return '¥'+Number(v||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}
function pv(v,d){return (v==null||isNaN(v))?'—':Number(v).toFixed(d);}
function pct(v,d){return v==null?'—':(Number(v).toFixed(d)+'%');}
function pCell(v){if(v==null)return'<span class="nsig">—</span>';return`<span class="${v<0.05?'sig':'nsig'}">${v.toFixed(3)}</span>`;}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
// 三态之「失败态」：给出可点的重试入口（DESIGN.md:98 要求失败须有原因 + 重试）
function retryBtn(call){/* data-retry 由全局点击委托分发（见下方 document click），不再内联 onclick —— 去 eval 风险 */return ' <button class="btn mini u-ml6" data-retry="'+esc(call)+'">重试</button>';}


// ========== 全局点击委托（2026-09-22）：替代内联 onclick ==========
// 数据按钮用 data-action + data-* 传参；重试按钮用 data-retry 传原调用串。
// 函数都在顶层（function/const 声明），点击时已在作用域内，直接 switch 调用，零 eval。
document.addEventListener('click', function(e){
  const t = e.target;
  const el = (t && t.closest) ? t.closest('[data-action],[data-retry]') : null;
  if(!el) return;
  if(el.dataset.action){
    const id = el.dataset.id, op = el.dataset.op, mode = el.dataset.mode, code = el.dataset.code;
    switch(el.dataset.action){
      case 'showPool': showPool(mode||'type'); break;
      case 'dcaRun': dcaRun(id==='all'?'all':Number(id)); break;
      case 'dcaBackfill': dcaBackfill(Number(id)); break;
      case 'dcaToggle': dcaToggle(Number(id), op); break;
      case 'dcaAdd': dcaAdd(); break;
      case 'dcaSync': dcaSync(); break;
      case 'editPlanMeta': editPlanMeta(); break;
      case 'addPlanItem': addPlanItem(); break;
      case 'updateNavThenReload': updateNavThenReload(); break;
      case 'reloadHoldings': reloadHoldings(false); break;
      case 'actBuy': actBuy(); break;
      case 'editPlanItem': if(el.dataset.prevent) e.preventDefault(); editPlanItem(Number(id), code); break;
      case 'actHolding': actHolding(op, Number(id)); break;
    }
    return;
  }
  const r = el.dataset.retry;
  if(r){
    switch(r){
      case 'loadDca()': loadDca(); break;
      case 'boot()': boot(); break;
      case "showPool('board')": showPool('board'); break;
      case 'loadSentiment()': loadSentiment(); break;
      case 'loadSectors(true)': loadSectors(true); break;
      case 'loadQuantModels()': loadQuantModels(); break;
    }
  }
});

function tempMeta(t){
  if(t<=20)return['cold','极冷','大胆加仓','var(--primary)']
  if(t<=40)return['cool','偏冷','适度加仓','var(--primary-hover)']
  if(t<=60)return['normal','适中','保持定投','var(--up)']
  if(t<=80)return['warm','偏热','减少买入','var(--warn)']
  return['hot','过热','考虑减仓','var(--down)']
}
function sparkSVG(navs,color){
  if(!navs||navs.length<2)return'<svg viewBox="0 0 46 18"><line x1="0" y1="9" x2="46" y2="9" stroke="var(--hairline)" stroke-width="1"/></svg>';
  const vals=navs.map(n=>n.nav); const min=Math.min(...vals),max=Math.max(...vals),rng=max-min||1;
  const pts=vals.map((v,i)=>`${(i/(vals.length-1))*46},${18-(v-min)/rng*16-1}`);
  return`<svg viewBox="0 0 46 18"><polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}
function riskBadge(risk){
  if(!risk)return'';
  const txt=risk.replace('','').replace('','').replace('','');
  const cls=risk.includes('稳健')?'b-green':(risk.includes('注意')?'b-yellow':'b-red');
  return`<span class="badge ${cls}">${esc(txt)}</span>`;
}

const STATE={all:null, current:'overview', seen:{overview:0,position:0,pool:0,sector:0,quant:0}};
// 深链：/?view=position 直接打开指定面板（便于分享与截图核对）
const VIEWS=['overview','position','pool','sector','quant'];
const QV=(typeof location!=='undefined')
  ? ((new URLSearchParams(location.search).get('view')||'').trim())
  : '';
const START_VIEW=VIEWS.includes(QV)?QV:'overview';
let ready=false;

function card(title,sub,inner,cls){return`<div class="card ${cls||''}"><h2>${title}${sub?`<span class="sub">${sub}</span>`:''}</h2>${inner}</div>`}

// ========== 交互操作（写持仓 / 定投 / 提示） ==========
function postJSON(url, body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})
    .then(r=>r.json()).catch(()=>({ok:false,error:'网络错误'}));
}
function toast(msg, isOk){
  const box=document.getElementById('toast'); if(!box)return;
  const d=document.createElement('div');
  d.className='toast '+(isOk?'ok':'err'); d.textContent=msg;
  box.appendChild(d); setTimeout(()=>d.remove(), isOk?4500:8000);
}
function forceRecalc(){ sessionStorage.setItem('dash_dirty','1'); location.reload(); }
function todayStr(){ const d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }

// ---- 投资计划维护（写库后局部刷新计划卡，不跳转） ----
async function reloadPlan(){
  const r=await fetch('/api/plan'); const g=await r.json();
  if(!g.ok){toast(g.error||'计划读取失败',false);return;}
  const plan=g.data.plan||{};
  if(STATE.all)STATE.all.plan=plan;
  const pc=document.getElementById('plan-card'); if(pc)pc.innerHTML=planHTML(plan);
  const kp=document.getElementById('kpi-plan'); if(kp)kp.textContent=fmtMoney(plan.total_invested||0);
  const ks=document.getElementById('kpi-plan-sub');
  if(ks){const tgt=(plan.total_capital||0)-(plan.cash_reserve||0); ks.textContent='目标 '+fmtMoney(tgt);}
}
async function editPlanMeta(){
  const p=(STATE.all&&STATE.all.plan)||{};
  const name=prompt('计划名称：', p.name||''); if(name===null)return;
  const goal=prompt('投资目标：', p.goal||''); if(goal===null)return;
  const tc=prompt('总资金（元）：', p.total_capital||''); if(tc===null)return;
  const cash=prompt('现金弹药/底仓（元）：', p.cash_reserve||''); if(cash===null)return;
  const horizon=prompt('计划周期（如 6个月）：', p.horizon||''); if(horizon===null)return;
  const risk=prompt('风险偏好（如 稳健偏平衡）：', p.risk_pref||''); if(risk===null)return;
  const j=await postJSON('/api/plan',{action:'update',name,goal,total_capital:tc,cash_reserve:cash,horizon,risk_pref:risk});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)reloadPlan();
}
async function addPlanItem(){
  const code=prompt('基金代码：'); if(!code)return;
  const name=prompt('基金名称（留空自动补全）：','')||'';
  const role=prompt('角色（如 黄金对冲 / 宽基底仓）：','')||'';
  const ta=prompt('目标金额（元）：'); if(ta===null)return;
  const tp=prompt('目标占比（%，可留空）：','')||'';
  const j=await postJSON('/api/plan',{action:'add_item',code,name,role,target_amount:ta,target_pct:tp});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)reloadPlan();
}
async function editPlanItem(itemId,code){
  if(!itemId){toast('该条目缺少ID，无法修改（可删除后重加）',false);return;}
  const ta=prompt('新的目标金额（元，回车=不变）：',''); if(ta===null)return;
  const tp=prompt('新的目标占比（%，回车=不变）：',''); if(tp===null)return;
  const role=prompt('新的角色说明（回车=不变）：',''); if(role===null)return;
  const body={action:'update_item',item_id:itemId};
  if(ta)body.target_amount=ta; if(tp)body.target_pct=tp; if(role)body.role=role;
  if(!body.target_amount&&!body.target_pct&&!body.role){toast('未修改任何字段',false);return;}
  const j=await postJSON('/api/plan',body);
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)reloadPlan();
}
async function delPlanItem(itemId){
  if(!itemId){toast('该条目缺少ID，无法删除',false);return;}
  if(!confirm('确认从计划中删除该基金条目？'))return;
  const j=await postJSON('/api/plan',{action:'delete_item',item_id:itemId});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)reloadPlan();
}
// ---- 局部刷新：逐日重放重算持仓（不整页跳转） ----
let NAVINFO={stale:false,latest:null};
async function reloadHoldings(silent){
  const j=await postJSON('/api/holdings/refresh',{});
  if(!j.ok){ toast(j.error||'刷新失败',false); return; }
  const d=j.data||{}, port=d.portfolio||{};
  NAVINFO={stale:!!d.stale,latest:d.nav_latest_date};
  if(STATE.all)STATE.all.portfolio=port;
  // 计划进度也会随买入/定投变化 → 先刷新计划卡与 STATE.all.plan，
  // 否则持仓行里的"计划 目标/已投 %"会一直用旧值（原先只有整页重载才更新）。
  try{ await reloadPlan(); }catch(e){}
  // 局部替换持仓卡（含每行金额/曲线/待确认状态）
  const hc=document.getElementById('holdings-card');
  if(hc)hc.innerHTML=holdingsInner(port, (STATE.all&&STATE.all.plan)||{});
  // 总览 KPI 同步：市值 / 盈亏 / 总资产 / 已实现
  const setT=(id,v)=>{const el=document.getElementById(id); if(el)el.textContent=v;};
  const setH=(id,v)=>{const el=document.getElementById(id); if(el)el.innerHTML=v;};
  const pendAmt=(port.holdings||[]).filter(h=>h.status==='pending_confirm').reduce((s,h)=>s+(h.buy_amount||0),0);
  setT('kpi-mv', fmtMoney(port.total_market_value));
  // 总资产 = 当前在仓市值（不叠加计划 cash_reserve，与 overviewKpis 同口径）
  setT('kpi-assets', fmtMoney(port.total_market_value||0));
  setH('kpi-assets-sub', '实时市值 '+fmtMoney(port.total_market_value)+(pendAmt>0?(' · 在途 '+fmtMoney(pendAmt)):''));
  setH('kpi-mv-sub', port.has_holdings?('盈亏 <span class="'+(port.total_pnl>=0?'pnl-pos':'pnl-neg')+'">'+fmtPct(port.total_return_pct)+'</span>'):'暂无持仓');
  setT('kpi-pnl', fmtMoney(port.total_pnl||0));
  setH('kpi-pnl-sub', '收益率 '+fmtPct(port.total_return_pct||0));
  const RZ=port.realized||{};
  setT('kpi-realized', fmtMoney(RZ.total_pnl||0));
  setH('kpi-realized-sub', RZ.count?('已了结 '+RZ.count+' 笔'+(RZ.total_fee?(' · 赎回费 '+fmtMoney(RZ.total_fee)):'')):'暂无了结');
  if(!silent)toast('已按买入确认日逐日重算',true);
  // 组合曲线同步刷新（局部）
  fetch('/api/portfolio/curve').then(r=>r.json()).then(k=>{
    if(k&&k.ok){ if(STATE.all)STATE.all.curve=k.data;
      const cc=document.getElementById('curve-card');
      if(cc)cc.outerHTML=curveCard(k.data); }
  }).catch(()=>{});
  // 调仓建议较慢：后台重算后局部替换
  fetch('/api/rebalance?fresh=1').then(r=>r.json()).then(k=>{
    if(k&&k.ok&&STATE.all){ STATE.all.rebalance=k.data;
      const rc=document.getElementById('rebalance-card');
      if(rc)rc.innerHTML=rebalanceInner(k.data,STATE.all.portfolio); }
  }).catch(()=>{});
}
async function updateNavThenReload(){
  if(!confirm('将联网更新当前持仓基金的净值（可能几十秒），继续？'))return;
  toast('正在更新净值…',true);
  const j=await postJSON('/api/nav/update',{});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)reloadHoldings(true);
}

// ---- 持仓操作（写库后局部刷新，不跳转） ----
async function actBuy(){
  const code=(document.getElementById('buy-code')?.value||'').trim();
  const amount=parseFloat(document.getElementById('buy-amount')?.value||'0');
  const date=(document.getElementById('buy-date')?.value||'').trim()||todayStr();
  const name=(document.getElementById('buy-name')?.value||'').trim();
  const after=document.getElementById('buy-after')?.checked||false;
  if(!code||!amount||amount<=0){toast('请填写基金代码和大于0的金额',false);return;}
  const j=await postJSON('/api/holdings',{action:'buy',code,name,amount,date,after_cutoff:after});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)reloadHoldings(true);
}
async function actHolding(action,id){
  if(!confirm(`确认对持仓 #${id} 执行「${action==='sell'?'减仓(卖出)':action==='update'?'修改':action==='delete'?'删除':'?'}」？`))return;
  if(action==='sell'){
    const amount=parseFloat(prompt('卖出金额（元）：')||'0');
    const date=(prompt('卖出日期（YYYY-MM-DD，回车=今天）：')||'').trim();
    if(!amount||amount<=0){toast('金额需大于0',false);return;}
    const j=await postJSON('/api/holdings',{action:'sell',id,amount,date});
    toast(j.message||j.error||'完成',!!j.ok); if(j.ok)reloadHoldings(true);
  }else if(action==='update'){
    const a=prompt('新的投入金额（元，回车=不变）：');
    const d=prompt('新的买入日期（YYYY-MM-DD，回车=不变）：');
    const body={action:'update',id};
    if(a&&parseFloat(a)>0)body.amount=parseFloat(a);
    if(d&&d.trim())body.date=d.trim();
    if(!body.amount&&!body.date){toast('未修改任何字段',false);return;}
    const j=await postJSON('/api/holdings',body);
    toast(j.message||j.error||'完成',!!j.ok); if(j.ok)reloadHoldings(true);
  }else if(action==='delete'){
    const j=await postJSON('/api/holdings',{action:'delete',id});
    toast(j.message||j.error||'完成',!!j.ok); if(j.ok)reloadHoldings(true);
  }
}

// ========== 定投管理（页面内渲染与操作） ==========
function dcaItemHTML(p){
  const st=p.status==='active'?'active':'paused';
  const lbl=st==='active'?'进行中':'已暂停';
  const exp=p.expected_periods, done=p.executed_periods, pend=p.pending_periods;
  const un=p.unlinked_periods||0;
  const cnt=(exp!=null)?`<span class="pill">应投 ${exp}</span><span class="pill u-tgreen">已投 ${done}</span>${
      pend?`<span class="pill u-pillwarn">待补录 ${pend}</span>`:''}${
      un?`<span class="pill u-pillwarn" title="这些期次标着已执行，但没有对应的买入凭证（旧口径遗留）。先跑 scripts/link_dca_periods.py 看补链方案，确认后再 --apply。">缺凭证 ${un}</span>`:''}`:'';
  return `<div class="dca-item"><div class="bar-row">
      <span class="u-ttext"><b>${esc(p.fund_name)}</b> <span class="fund-code">${esc(p.fund_code)}</span> <span class="chip ${st}">${lbl}</span></span>
      <span class="b2">每期 ${fmtMoney(p.amount_per_period)} / ${esc(p.frequency)}</span></div>
    <div class="bar-row u-mt4"><span>${cnt}</span>
      <span class="qtag">已投 ${p.total_periods} 期 · 累计 ${fmtMoney(p.total_amount)} · 下期 ${esc(p.next_run_date||'-')} ${p.due?'· 到期':''}${p.last_synced_at?` · 同步于 ${esc(p.last_synced_at)}`:''}</span></div>
    <div class="act-row u-mt6">
      <button class="btn mini ok"${p.due?'':' disabled'} title="${p.due?'可执行一期':'尚未到期'}" data-action="dcaRun" data-id="${p.id}">执行${p.due?'到期':'一期'}</button>
      <button class="btn mini"${pend?'':' disabled'} title="${pend?'补录所有待投期次':'无待补录'}" data-action="dcaBackfill" data-id="${p.id}">补录${pend?' '+pend+' 期':''}</button>
      <button class="btn mini" data-action="dcaToggle" data-id="${p.id}" data-op="${st==='active'?'pause':'resume'}">${st==='active'?'暂停':'恢复'}</button>
      <button class="btn mini danger" data-action="dcaToggle" data-id="${p.id}" data-op="delete">删除</button>
    </div></div>`;
}
async function loadDca(){
  const el=document.getElementById('dca-wrap');
  if(!el)return;
  try{
    const r=await fetch('/api/dca'); const j=await r.json();
    if(!j.ok)throw new Error(j.error||'fail');
    const plans=j.data||[];
    const sel=(v,s)=>`<option value="${v}" ${v===s?'selected':''}>${v}</option>`;
    let h=`<div class="frow" style="margin:4px 0 10px">
      <input id="dca-code" placeholder="基金代码" size="9">
      <input id="dca-name" placeholder="名称(留空自动补全)" size="11">
      <input id="dca-amount" type="number" min="0" step="0.01" placeholder="每期金额" style="min-width:90px">
      <select id="dca-freq" style="background:var(--bg2);border:1px solid var(--border);border-radius:7px;color:var(--text);padding:5px">
        ${sel('daily','')}${sel('weekly','weekly')}${sel('biweekly','')}${sel('monthly','')}
      </select>
      <input id="dca-date" type="date" placeholder="开始日期(默认今天)">
      <button class="btn ok" data-action="dcaAdd">添加定投</button>
    </div>
    <div class="act-row"><span class="qtag">频率：daily=日 / weekly=周 / biweekly=双周 / monthly=月（非交易日不顺延扣款）</span>
      <button class="btn mini primary u-mlauto" data-action="dcaSync">同步定投期次</button>
      <button class="btn mini" data-action="dcaRun" data-id="all" ${plans.some(p=>p.due&&p.status==='active')?'':'disabled'}>执行全部到期</button></div>`;
    if(!plans.length){ h+='<div class="empty">暂无定投计划 · 上方添加</div>'; }
    else{ for(const p of plans)h+=dcaItemHTML(p); }
    el.innerHTML=h;
  }catch(e){ el.innerHTML='<div class="empty">定投计划加载失败'+retryBtn('loadDca()')+'</div>'; }
}
async function dcaAdd(){
  const code=(document.getElementById('dca-code')?.value||'').trim();
  const name=(document.getElementById('dca-name')?.value||'').trim();
  const amount=parseFloat(document.getElementById('dca-amount')?.value||'0');
  const frequency=(document.getElementById('dca-freq')?.value||'weekly');
  const date=(document.getElementById('dca-date')?.value||'').trim();
  if(!code||!amount||amount<=0){toast('请填写基金代码和每期金额',false);return;}
  const j=await postJSON('/api/dca',{action:'add',code,name,amount,frequency,date});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)loadDca();
}
async function dcaSync(){
  if(!confirm('同步定投期次？会按交易日历推算应投期数，并自动补录最近到期的 1 期（其余标记待补录）。'))return;
  const j=await postJSON('/api/dca',{action:'sync'});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok){ loadDca(); reloadHoldings(true); }
}
async function dcaBackfill(id){
  if(!confirm('补录该计划所有“待补录”期次？（每期会各记一笔买入）'))return;
  const j=await postJSON('/api/dca',{action:'backfill',id});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok){ loadDca(); reloadHoldings(true); }
}
async function dcaRun(id){
  const confirmTxt= id==='all'? '确认执行所有到期的定投？（各记一笔买入）':'确认执行该定投一期？（会记录一笔买入）';
  if(!confirm(confirmTxt))return;
  const j=await postJSON('/api/dca',{action:'run',id});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok){ loadDca(); reloadHoldings(true); }
}
async function dcaToggle(id,action){
  const lbl= action==='delete'?'删除该定投计划':'切换暂停/恢复';
  if(!confirm('确认'+lbl+'？'))return;
  const j=await postJSON('/api/dca',{action,id});
  toast(j.message||j.error||'完成',!!j.ok);
  if(j.ok)loadDca();
}

// ========== data load ==========
async function refresh(){
  document.getElementById('updateTime').textContent='加载中...';
  await loadAll(false);                 // 常规刷新：命中缓存，保持当前视图
  document.getElementById('updateTime').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN');
}
async function loadAll(fresh,_attempt){
  const app=document.getElementById('app');
  const attempt=_attempt||0;
  if(!ready)app.innerHTML='<div class="loading"><span class="spinner"></span>正在加载数据...</div>';
  try{
    const r=await fetch('/api/all'+(fresh?'?fresh=1':''));
    const j=await r.json();
    // 冷启动不阻塞（2026-09-22）：全市场快照后筛选池冷算 ~110s，服务端返回 warming，
    // 由前端按 retry_in 轮询 —— 与 /api/sectors 同一套模式，绝不挂 180 秒等超时。
    if(j.status==='warming'){
      const wait=Math.max(5,Math.min(j.retry_in||15,30));
      app.innerHTML='<div class="card"><h2>正在准备数据</h2><div class="empty">'
        +'首次启动需要计算筛选池（全市场约 1–2 分钟，之后走缓存）。'
        +'<span class="spinner"></span> 第 '+(attempt+1)+' 次等待，'+wait+' 秒后自动重试</div></div>';
      if(attempt<14){ await sleep(wait*1000); return loadAll(fresh,attempt+1); }
      app.innerHTML='<div class="card"><h2>计算时间偏长</h2><div class="empty">后台仍在计算，可稍后手动刷新。'+retryBtn('boot()')+'</div></div>';
      return;
    }
    if(!j.ok){app.innerHTML='<div class="card"><h2>加载失败</h2><div class="empty u-tdown">'+esc(j.error||j.data?.error||'未知错误')+retryBtn('boot()')+'</div></div>';return;}
    STATE.all=j.data; ready=true;
    renderApp();
    const tu=document.getElementById('updateTime'); if(tu)tu.textContent='更新于 '+new Date().toLocaleTimeString('zh-CN');
    // 后台预热各异步面板，避免打开时等待
    loadSentiment(); loadSectors(); loadQuantModels();
  }catch(e){
    if(!ready)app.innerHTML='<div class="card"><h2>连接失败</h2><div class="empty">python src/main.py web 是否在运行？'+retryBtn('boot()')+'</div></div>';
  }
}

// ========== view switch ==========
function showView(v){
  if(!ready && v!=='overview')return;      // 完整数据未就绪前只允许总览
  STATE.current=v;
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.dataset.view===v));
  document.querySelectorAll('.side-a').forEach(a=>{
    const on=a.dataset.v===v; a.classList.toggle('active',on);
    if(on)a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  STATE.seen[v]=1;
  // 首次进入该面板时触发对应懒加载
  if(v==='sector'&&document.getElementById('sector-card')&&!document.getElementById('sector-card').dataset.done)loadSectors();
  if(v==='quant'&&document.getElementById('quant-card')&&!document.getElementById('quant-card').dataset.done)loadQuantModels();
  if(v==='overview'&&document.getElementById('sentiment-card')&&!document.getElementById('sentiment-card').dataset.done)loadSentiment();
}
document.querySelectorAll('.side-a').forEach(a=>a.addEventListener('click',()=>showView(a.dataset.v)));

// ========== render main (all views as sections) ==========
function renderApp(){
  const d=STATE.all; if(!d)return;
  const T=d.temp||{}, F=d.funds||{funds:[],summary:{}}, P=d.portfolio||{}, PL=d.plan||{funds:[]}, RB=d.rebalance||{};
  const hasHold=P.has_holdings, hasPlan=!!(PL.funds&&PL.funds.length);
  let out='';

  // ---------- 总览 ----------（单一实现见 overviewSectionHTML，消除双渲染路径）
  out+=overviewSectionHTML(T,P,PL,d.stats,d.curve);

  // ---------- 持仓 / 计划 / 调仓 ----------
  let inner='';
  inner+=`<div class="card full" id="plan-card">${planHTML(PL)}</div>`;
  inner+=`<div class="card full" id="holdings-card">${holdingsHTML(P,PL)}</div>`;
  inner+=`<div class="card full" id="rebalance-card">${rebalanceInner(RB,P)}</div>`;
  inner+=card('定投计划','添加 / 暂停恢复 / 执行到期期数 / 删除', '<div id="dca-wrap"><div class="loading"><span class="spinner"></span>加载定投计划...</div></div>','full');
  out+=`<section class="view" data-view="position">${secHead('持仓 · 计划 · 调仓','你的投资组合与执行建议')}<div class="grid">${inner}</div></section>`;

  // ---------- 筛选池 ----------
  out+=`<section class="view" data-view="pool">${secHead('基金质量筛选池','不推荐“买哪只”，只排除有坑的')}<div class="grid"><div class="card full" id="pool-card">${poolHTML(F)}</div></div></section>`;

  // ---------- 板块 ----------
  out+=`<section class="view" data-view="sector">${secHead('行业板块排名','31个申万一级行业 · 动量+趋势+风险')}
    <div class="grid">${card('板块排名','','<div id="sector-card"><div class="loading"><span class="spinner"></span>板块数据加载中...</div></div>','full')}</div></section>`;

  // ---------- 量化模型 ----------
  out+=`<section class="view" data-view="quant">${secHead('量化模型','vol 预测 · 回撤预警 · 组合模拟（OOS 2023-2026）')}
    <div class="grid">${card('量化模型','','<div id="quant-card"><div class="loading"><span class="spinner"></span>模型结果加载中...</div></div>','full')}</div></section>`;

  out+=`<div class="foot"><span>操作均在页面内完成并写入本地数据库；加仓/减仓后会自动重算持仓与调仓建议。</span></div>`;
  document.getElementById('app').innerHTML=out;
  showView(ready ? START_VIEW : STATE.current);
  // 定投卡片内容异步填充
  if(document.getElementById('dca-wrap'))loadDca();
}

function kpi(lb,vv,su,color,vid,sid){
  return`<div class="k"><div class="lb">${lb}</div><div class="vv" ${vid?`id="${vid}"`:''} ${color?`style="color:${color}"`:''}>${vv}</div><div class="su" ${sid?`id="${sid}"`:''}>${su||''}</div></div>`;
}

// 总览 KPI（总资产/累计收益/温度/仓位/市值/计划投入）—— 两条渲染路径共用，避免口径不一致
function overviewKpis(T,P,PL,S){
  S=S||{};
  const pnl=(S.total_pnl!=null)?S.total_pnl:(P.total_pnl||0);
  const ret=(S.total_return_pct!=null)?S.total_return_pct:(P.total_return_pct||0);
  const assets=(S.total_assets!=null)?S.total_assets:(P.total_market_value||0);
  const pend=(S.pending_amount||0);
  const tgt=(PL.total_capital||0)-(PL.cash_reserve||0);
  // 后端的 level_desc 自带 emoji（TEMP_LEVELS）→ 在展示层用 esc() 剥离并转义
  return `<div class="kpi">
      ${kpi('总资产', fmtMoney(assets), '实时市值 '+fmtMoney(P.total_market_value)+(pend>0?(' · 在途 '+fmtMoney(pend)):''), null, 'kpi-assets', 'kpi-assets-sub')}
      ${kpi('累计收益', fmtMoney(pnl), '收益率 '+fmtPct(ret), (pnl>=0?'var(--green)':'var(--red)'), 'kpi-pnl', 'kpi-pnl-sub')}
      ${kpi('已实现收益', fmtMoney(S.realized_pnl||0), (S.realized_count?('已了结 '+S.realized_count+' 笔'+(S.realized_fee?(' · 赎回费 '+fmtMoney(S.realized_fee)):'')):'暂无了结'), ((S.realized_pnl||0)>=0?'var(--green)':'var(--red)'), 'kpi-realized', 'kpi-realized-sub')}
      ${kpi('市场温度', (T.temperature==null?'数据不足':fmt(T.temperature)+'°'), esc(T.level_desc||''), (T.temperature==null?'var(--dim)':tempMeta(T.temperature)[3]))}
      ${kpi('建议权益仓位', (T.target_equity_pct==null?'—':(T.target_equity_pct+'%')), (T.target_equity_pct==null?'温度数据不足':('固收 '+fmt(100-T.target_equity_pct)+'%')))}
      ${kpi('持仓市值', fmtMoney(P.total_market_value), P.has_holdings? ('盈亏 '+fmtPct(P.total_return_pct)):'暂无持仓', null, 'kpi-mv', 'kpi-mv-sub')}
      ${kpi('计划投入', fmtMoney(PL.total_invested||0), '目标 '+fmtMoney(tgt), null, 'kpi-plan', 'kpi-plan-sub')}
    </div>
    <div class="qtag u-mt8">口径：历史持仓成本按旧口径（确认日 T+1 净值）；新流水按申请日 T 净值定价。已实现收益含赎回费（持有 &lt;7 天按 1.5%）。<br>「收益」= 金额法（市值 − 剩余成本）÷ 剩余成本，即<b>我赚了多少</b>；与「基金净值涨跌」（净值比值法，不含份额舍入）会有约 0.1% 的差 —— 那是份额四舍五入到 2 位造成的，两数不可混用。</div>`;
}

function portfolioChartSVG(C){
  const W=760,H=185,l=10,r=10,t=16,b=24;
  const n=(C.dates||[]).length;
  if(n<2)return'';
  // 批次5.1：主图画资金加权收益率（return_pct = pnl/cost），不再画市值/成本
  // （建仓期每日定投会使市值单调上升，画市值与真实盈亏无关且把收益率压成一条缝）
  const vals=(C.return_pct||[]).map(v=>Number(v)||0);
  if(vals.length!==n)return'';
  const mx=Math.max(0,...vals), mn=Math.min(0,...vals);
  const M=Math.max(Math.abs(mx),Math.abs(mn))||1;          // y 轴关于 0 对称 [-M,+M]，零轴居中
  const x=i=>l+i*(W-l-r)/(n-1);
  const y=v=>t+(1-(v+M)/(2*M))*(H-t-b);
  const pts=a=>a.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const y0=y(0).toFixed(1);                                  // 零轴（盈亏分界）
  const area=`${x(0).toFixed(1)},${y0} `+pts(vals)+` ${x(n-1).toFixed(1)},${y0}`;
  const step=Math.ceil(n/6);
  const lab=(C.dates||[]).map((d,i)=>i%step===0?`<text x="${x(i).toFixed(1)}" y="${H-7}" font-size="9" fill="var(--ink-tertiary)" text-anchor="${i===0?'start':i===n-1?'end':'middle'}">${esc(String(d).slice(5))}</text>`:'').join('');
  const last=vals[n-1], lastX=x(n-1).toFixed(1), lastY=y(last).toFixed(1);
  const col=(last>=0)?'var(--up)':'var(--down)';
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="组合收益率曲线">
    <defs>
      <linearGradient id="cgUp" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--up)" stop-opacity="0.28"/><stop offset="100%" stop-color="var(--up)" stop-opacity="0"/></linearGradient>
      <linearGradient id="cgDn" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--down)" stop-opacity="0"/><stop offset="100%" stop-color="var(--down)" stop-opacity="0.28"/></linearGradient>
      <clipPath id="cpUp"><rect x="0" y="0" width="${W}" height="${y0}"/></clipPath>
      <clipPath id="cpDn"><rect x="0" y="${y0}" width="${W}" height="${(H-Number(y0)).toFixed(1)}"/></clipPath>
    </defs>
    <text x="${l}" y="9" font-size="9" fill="var(--ink-tertiary)">收益率 %（资金加权：pnl/累计成本，非时间加权）</text>
    ${[M,-M].map(v=>`<text x="${W-r}" y="${(y(v)+(v>0?9:-2)).toFixed(1)}" font-size="9" fill="var(--ink-tertiary)" text-anchor="end">${(v>0?'+':'') + v.toFixed(1)}%</text>`).join('')}
    ${[M,-M].map(v=>`<line x1="${l}" y1="${y(v).toFixed(1)}" x2="${W-r}" y2="${y(v).toFixed(1)}" stroke="var(--hairline)" stroke-width="1"/>`).join('')}
    <line x1="${l}" y1="${y0}" x2="${W-r}" y2="${y0}" stroke="var(--ink-subtle)" stroke-width="1.4"/>
    <text x="${W-r}" y="${(Number(y0)-3).toFixed(1)}" font-size="9" fill="var(--ink-subtle)" text-anchor="end">0%</text>
    <polygon points="${area}" fill="url(#cgUp)" clip-path="url(#cpUp)"/>
    <polygon points="${area}" fill="url(#cgDn)" clip-path="url(#cpDn)"/>
    <polyline points="${pts(vals)}" fill="none" stroke="${col}" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${lastX}" cy="${lastY}" r="3" fill="${col}"/>
    ${lab}</svg>`;
}
function curveCard(C){
  C=C||{};
  // F-03：曲线只画"已起算"的持仓 → 被排除的（待确认 + 起算日未到）统一叫「未入仓」，
  // 并把笔数与金额写在卡片上，免得跟顶部 KPI 的"持仓市值"（含全部持仓）对比时以为算错了。
  const nIn=(C.excluded_not_in!=null)?C.excluded_not_in:(C.excluded_pending||0);
  const amtIn=C.excluded_amount||0;
  const notInTxt=nIn?(` · 另有 ${nIn} 笔未入仓${amtIn?`（约 ${fmtMoney(amtIn)}）`:''}未计入`):'';
  const n=(C.dates||[]).length;
  if(n<2){
    return `<div class="card full" id="curve-card"><h2>组合累计走势</h2><div class="empty">暂无可绘制的组合曲线${
      nIn?`${notInTxt}`:''}</div></div>`;
  }
  const lastRet=(C.return_pct||[]).slice(-1)[0];
  const lastPnl=(C.pnl||[]).slice(-1)[0];
  const lastVal=(C.value||[]).slice(-1)[0];
  const tone=(lastPnl>=0)?'var(--green)':'var(--red)';
  return `<div class="card full" id="curve-card"><h2>组合累计走势 <span class="sub">— 起点 ${esc(String(C.dates[0]))} · ${C.funds_used||0} 只持仓</span></h2>
    <div class="stat-line">当前市值 <b>${fmtMoney(lastVal)}</b> · 累计收益 <b style="color:${tone}">${fmtMoney(lastPnl)}</b> · 收益率 <b style="color:${tone}">${fmtPct(lastRet)}</b></div>
    <div class="pcurve">${portfolioChartSVG(C)}</div>
    <div class="qlegend"><span class="k"><span class="sw u-bgup"></span>零轴上方（盈利区间）</span>
      <span class="k"><span class="sw u-bgdown"></span>零轴下方（亏损区间）</span>
      ${nIn?`<span class="qtag">另有 ${nIn} 笔未入仓${amtIn?`（约 ${fmtMoney(amtIn)}）`:''}未计入</span>`:''}</div>
    <div class="qtag u-mt6">口径：本图只画「已起算」的持仓，起点为最早起算日、末点为净值末日；「未入仓」= 待确认买入 + 起算日晚于末点的持仓，它们尚未计入市值与成本，等确认/起算后会自动进来。图中收益率 = pnl / 累计成本，为资金加权持仓收益率（非时间加权）；与「已实现收益（含赎回费）」是两个不同口径，勿混用。</div></div>`;
}

function allocBars(title, obj, bg){
  const ent=Object.entries(obj||{});
  if(!ent.length)return'';
  let h=`<div class="qsec"><h3>${title}</h3>`;
  for(const [k,v] of ent){
    h+=`<div class="bar-row"><span>${esc(k)}</span><span class="b2">${pv(v,1)}%</span></div>
      <div style="height:6px;background:var(--card2);border-radius:3px;overflow:hidden;margin-bottom:7px">
        <div style="width:${Math.min(100,Number(v)||0)}%;height:100%;background:${bg};border-radius:3px"></div></div>`;
  }
  return h+'</div>';
}
function allocCard(S){
  S=S||{};
  const type=allocBars('持仓分布（按基金类型）', S.type_alloc, 'var(--ink-tertiary)');
  const board=allocBars('行业占比（按名称归类）', S.board_alloc, 'var(--primary)');
  if(!type&&!board)return'';
  const sub=S.pending_count?('待确认 '+S.pending_count+' 笔'):'';
  return card('结构分布', sub, `<div class="quant-grid">${type||'<div class="qtag">暂无数据</div>'}${board||'<div class="qtag">暂无数据</div>'}</div>`, 'full');
}

function ovTempCard(T){
  // tempMeta 返回 [level, label, action, color] 四项；原来解构了 5 个名字，
  // 导致 tAction 拿到颜色串 → 页面上直接显示 "var(--up)"，且 tColor 为 undefined。
  // 温度数据不足（全维度缺失，F-02）→ 不显示任何度数、不画进度条、不给建议。
  const insufficient=(T.temperature==null);
  const[tLvl,tLabel,tAction,tColor]=insufficient?['unknown','数据不足',(T.action||'数据不足，暂不给仓位建议'),'var(--dim)']:tempMeta(T.temperature);
  let inner= insufficient
    ? `<div class="u-flexbase">
      <span class="temp-big" style="color:${tColor}">—</span>
      <span class="temp-level" style="color:${tColor}">${tLabel}</span></div>
    <div class="u-sub">${esc(tAction)}</div>`
    : `<div class="u-flexbase">
      <span class="temp-big" style="color:${tColor}">${fmt(T.temperature)}°</span>
      <span class="temp-level" style="color:${tColor}">${tLabel}</span></div>
    <div class="temp-bar"><div class="temp-bar-fill" style="width:${T.temperature}%;background:${tColor}"></div></div>
    <div class="u-sub">${tAction}</div>`;
  inner+=`<div class="temp-detail">
      <span>PE分位 ${fmtDim(T.components?.pe_score)}°</span><span>PB分位 ${fmtDim(T.components?.pb_score)}°</span>
      <span>性价比 ${fmtDim(T.components?.erp_score)}°</span><span>量能 ${fmtDim(T.components?.volume_score)}°</span>
      <span>情绪 ${fmtDim(T.components?.sentiment_score)}°</span></div>
    ${(T.degraded_dimensions&&T.degraded_dimensions.length)?`<div class="divergence" style="border-left:3px solid var(--warn)">数据缺失维度已剔除并按剩余权重归一化：${esc(T.degraded_dimensions.join('、'))}</div>`:''}`;
  if(T.divergence){
    const dCol=T.divergence.level==='一致'?'var(--green)':(T.divergence.level.includes('轻微')?'var(--yellow)':'var(--red)');
    inner+=`<div class="divergence" style="border-left:3px solid ${dCol}">${esc(T.divergence.level)}：${esc(T.divergence.message||'')}</div>`;
  }
  if(T.market_style&&T.market_style.dominant!=='unknown')inner+=`<div class="style-info"><b>${esc(T.market_style.dominant)}</b> · ${esc(T.market_style.detail||'')}</div>`;
  return card('市场温度','本地快照',inner);
}

function ovAllocCard(T,P,PL){
  // 温度数据不足（全维度缺失，F-02）→ 目标仓位不存在，不给任何金额测算
  if(T.target_equity_pct==null){
    return card('仓位建议','',`<div class="stat-line u-mt8">市场温度数据不足 → 目标仓位无法确定，暂不给仓位建议。请先完成数据采集（<code>python src/main.py collect</code> / <code>index</code>）。</div>`);
  }
  const eq=T.target_equity_pct;
  // 无持仓时用投资计划的总本金作为测算基数，避免与计划卡口径冲突
  const base=P.has_holdings?(P.total_invested||0):((PL.total_capital||0));
  let inner=`<div class="alloc-bar">
      <div class="alloc-e" style="width:${eq}%">${eq}% 权益</div>
      <div class="alloc-b" style="width:${100-eq}%">${100-eq}% 固收</div></div>
    <div class="alloc-legend">
      <span>权益 ≈ ${fmtMoney(base*eq/100)}</span>
      <span>固收 ≈ ${fmtMoney(base*(100-eq)/100)}</span></div>`;
  if(P.has_holdings){
    inner+=allocChips(P.alloc);   // 投入/市值/盈亏已在总览 KPI 显示，此处不再重复
  }else{
    inner+=`<div class="stat-line u-mt12">暂无持仓 · 金额按投资计划总本金测算</div>`;
  }
  return card('仓位建议','',inner);
}
function allocChips(alloc){
  if(!alloc)return'';
  let h='<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">';
  for(const[k,v]of Object.entries(alloc)){h+=`<span class="pill u-bgdim">${esc(k)} ${pv(v,0)}%</span>`;}
  return h+'</div>';
}

function planHTML(PL){
  const invested=PL.total_invested||0;
  const target=(PL.total_capital||0)-(PL.cash_reserve||0);
  const pct=target>0?Math.min(100,invested/target*100):0;
  const funds=PL.funds||[];
  let h=`<h2>投资计划 <span class="sub">— ${esc(PL.name||'')} · 起始 ${esc(PL.start_date||'')}</span>
      <span class="u-right">
        <button class="btn mini" data-action="editPlanMeta">编辑计划</button>
        <button class="btn mini" data-action="addPlanItem">添加基金</button>
      </span></h2>
    <div class="act-row" style="margin:0 0 10px">
      <span class="qtag">${esc(PL.goal||'未填目标')}</span>
      <span class="qtag">周期 ${esc(PL.horizon||'—')}</span>
      <span class="qtag">风险偏好 ${esc(PL.risk_pref||'—')}</span>
      ${PL.notes?`<span class="qtag">备注：${esc(PL.notes)}</span>`:''}
    </div>
    <div class="bar-row">
      <span>权益建仓总进度　<b class="u-tink">${fmtMoney(invested)}</b> / ${fmtMoney(target)}　<span class="b2">${pv(pct,1)}%</span></span>
      <span class="b2">总资金 ${fmtMoney(PL.total_capital||0)} · 现金弹药 ${fmtMoney(PL.cash_reserve||0)}</span>
    </div>
    <div class="prog" style="height:8px"><div style="width:${Math.min(100,pct)}%;background:var(--primary)"></div></div>
    <div class="qtag u-mt8">各基金的目标与进度已并入下方「持仓明细」逐只显示（不再重复列出）。计划基金 ${funds.length} 只。</div>`;
  return h;
}

function navHintHTML(){
  const txt=NAVINFO.latest?('净值截至 '+esc(NAVINFO.latest)):'本地无净值';
  const warn=NAVINFO.stale?`<span class="u-tyellow">· 落后于今日，金额可能偏旧</span>
     <button class="btn mini" data-action="updateNavThenReload">更新净值</button>`:'';
  return `<div class="hint" id="nav-hint">${txt} ${warn}</div>`;
}
function curveSVG(curve,color){
  if(!curve||curve.length<2)return'<span class="qtag">—</span>';
  const vals=curve.map(c=>c.nav), mn=Math.min(...vals), mx=Math.max(...vals), rg=mx-mn||1;
  const W=110,H=26;
  const pts=vals.map((v,i)=>`${(i/(vals.length-1))*W},${H-(v-mn)/rg*(H-3)-1.5}`).join(' ');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:110px;height:26px"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/></svg>`;
}
// 按基金汇总（一级展示），每只基金下挂“分笔明细”（二级折叠）
function groupHoldingsByFund(holds){
  const m=new Map();
  for(const h of holds){
    const k=h.code||h.name||'?';
    if(!m.has(k))m.set(k,{code:h.code,name:h.name||h.code,lots:[],shares:0,cost:0,value:0,pnl:0,
                          curve:[],pendingN:0,pendingCost:0,estNum:0,estDen:0});
    const g=m.get(k); g.lots.push(h);
    g.shares+=Number(h.shares||0); g.cost+=Number(h.buy_amount||0);
    g.value+=Number(h.current_value||0); g.pnl+=Number(h.pnl||0);
    if((!g.curve||!g.curve.length)&&h.curve&&h.curve.length)g.curve=h.curve;
    if(h.status==='pending_confirm'){
      g.pendingN++; g.pendingCost+=Number(h.buy_amount||0);
      if(h.pending_est_pct!=null){
        g.estNum+=Number(h.buy_amount||0)*Number(h.pending_est_pct);
        g.estDen+=Number(h.buy_amount||0);
      }
    }
  }
  const out=[...m.values()];
  for(const g of out){
    g.pnl_pct=g.cost? g.pnl/g.cost*100 : 0;
    g.allPending=g.lots.every(l=>l.status==='pending_confirm');
    g.estPct=g.estDen? g.estNum/g.estDen : null;
    g.lots.sort((a,b)=>String(a.apply_date||a.buy_date).localeCompare(String(b.apply_date||b.buy_date)));
  }
  return out.sort((a,b)=>b.value-a.value);
}
function holdingsHTML(P,PL){
  let h=`<h2>持仓明细 <span class="sub">— 按基金汇总 · 展开看分笔；收益自各自起算日算起</span>
      <button class="btn mini u-right" data-action="reloadHoldings">刷新涨跌</button></h2>
    <div class="stat-line"><b>投入</b> ${fmtMoney(P.total_invested)} · <b>市值</b> ${fmtMoney(P.total_market_value)} · <b class="${P.total_pnl>=0?'pnl-pos':'pnl-neg'}">${fmtPct(P.total_return_pct)}</b>（累计 ${fmtMoney(P.total_pnl)}）</div>
    ${navHintHTML()}`;
  // 加仓 / 买入新基金 表单
  h+=`<div style="margin:10px 0;padding:10px 12px;background:var(--surface-2);border:1px solid var(--hairline);border-radius:var(--radius-md)">
    <div class="u-w600-mb6">加仓 / 买入新基金</div>
    <div class="frow">
      <input id="buy-code" placeholder="基金代码 如 000011" size="9">
      <input id="buy-name" placeholder="名称(留空自动补全)" size="13">
      <input id="buy-amount" type="number" min="0" step="0.01" placeholder="金额(元)" style="min-width:96px">
      <input id="buy-date" type="date" placeholder="日期(默认今天)">
      <label class="qtag" style="display:inline-flex;align-items:center;gap:4px">
        <input type="checkbox" id="buy-after" style="min-width:auto"> 15:00后提交</label>
      <button class="btn primary" data-action="actBuy">记买入</button>
    </div></div>`;
  const holds=(P.holdings||[]).filter(x=>x.id!=null);
  if(!holds.length){ h+='<div class="empty">暂无持仓 · 用上方「加仓」录入第一笔</div>'; return h; }

  const groups=groupHoldingsByFund(holds);
  // 计划目标并入每只基金行（避免计划卡与持仓卡重复列同一批基金）
  const planMap={};
  for(const f of ((PL&&PL.funds)||[])) if(f.code) planMap[f.code]=f;
  h+=`<div class="lot-head"><span>基金（${groups.length} 只 / ${holds.length} 笔）</span><span>走势</span><span>市值</span><span>收益</span><span>笔数</span></div>`;
  for(const g of groups){
    const tone=g.pnl>=0?'var(--up)':'var(--down)';
    const badge=g.pendingN
      ? `<span class="badge ${g.allPending?'b-yellow':'b-yellow'}">${g.allPending?'待确认':'含待确认'} ${g.pendingN} 笔</span>` : '';
    const pf=planMap[g.code];
    const planLine=pf?`<div class="fg-plan">计划 ${pf.role?esc(pf.role)+' · ':''}目标 ${fmtMoney(pf.target)} · 已投 ${fmtMoney(pf.invested)} · ${pv(pf.progress_pct,0)}%${
        (pf.next&&pf.next.amount>0)?` · 下一笔 ${fmtMoney(pf.next.amount)}`:''}${
        pf.item_id?` <button class="btn mini" data-action="editPlanItem" data-prevent="1" data-id="${pf.item_id}" data-code="${esc(g.code)}">改计划</button>`:''}</div>`
      :'';
    let perf;
    if(g.allPending){
      perf=g.estPct!=null
        ? `<span style="color:${g.estPct>=0?'var(--up)':'var(--down)'}">预估 ${fmtPct(g.estPct)} / ${fmtMoney(g.pendingCost*g.estPct/100)}</span>`
        : `<span class="qtag">未起算</span>`;
    }else{
      perf=`<span style="color:${tone}">${fmtPct(g.pnl_pct)} / ${fmtMoney(g.pnl)}</span>`;
    }
    h+=`<details class="fund-group">
      <summary>
        <span class="fg-line"><span class="fg-code">${esc(g.code)}</span><span class="fg-name" title="${esc(g.name)}">${esc(g.name)}</span>${badge}${planLine}</span>
        <span class="curve">${curveSVG(g.curve,tone)}</span>
        <span class="num">${fmtMoney(g.value)}</span>
        <span class="num">${perf}</span>
        <span class="num qtag">${g.lots.length} 笔</span>
      </summary>
      <div class="lots-wrap"><table class="tbl">
        <thead><tr><th>申请日</th><th>定价日</th><th>金额</th><th>份额</th><th>确认日</th><th>起算日</th><th>状态</th><th>操作</th></tr></thead><tbody>`;
    for(const l of g.lots){
      const st=l.status==='pending_confirm'?(l.nav_missing?'待确认·净值未公布':'待确认'):(l.status==='sell_pending'?'卖出待确认':'持有');
      h+=`<tr>
        <td>${esc(l.apply_date||l.buy_date||'')}</td>
        <td>${esc(l.effective_date||'—')}</td>
        <td>${fmtMoney(l.buy_amount)}</td>
        <td>${(Number(l.shares)||0).toFixed(2)}</td>
        <td>${esc(l.confirm_date||'—')}${l.legacy_rule_deviation?`<span class="pill u-pillwarn" title="该笔按旧规则记账：确认日 ${esc(l.legacy_rule_deviation.stored)}。按现行规则（QDII T+2 确认 / 非交易日不再叠加 15:00 顺延）应为 ${esc(l.legacy_rule_deviation.current_rule)}。历史记录不回填，仅标注。">旧口径</span>`:''}</td>
        <td>${esc(l.accrual_start||'—')}</td>
        <td>${esc(st)}</td>
        <td class="u-nowrap">
          <button class="btn mini ok" ${l.status==='pending_confirm'?'disabled':''} data-action="actHolding" data-op="sell" data-id="${l.id}">减仓</button>
          <button class="btn mini" data-action="actHolding" data-op="update" data-id="${l.id}">改</button>
          <button class="btn mini danger" data-action="actHolding" data-op="delete" data-id="${l.id}">删</button>
        </td></tr>`;
    }
    h+=`</tbody></table></div></details>`;
  }
  // 已了结：把卖出后的落袋盈亏与赎回费摊开（原先这部分完全不在报表里）
  const RZ=P.realized||{};
  if(RZ.count){
    h+=`<details class="fund-group u-mt10"><summary style="grid-template-columns:1fr auto">
      <span class="fg-line"><span class="fg-name">已了结（${RZ.count} 笔）</span>
        <span class="qtag">已实现 <b style="color:${(RZ.total_pnl||0)>=0?'var(--up)':'var(--down)'}">${fmtMoney(RZ.total_pnl)}</b> · 赎回费合计 ${fmtMoney(RZ.total_fee)}</span></span>
      <span class="num qtag">展开明细</span></summary>
      <div class="lots-wrap"><table class="tbl">
      <thead><tr><th>确认日</th><th>基金</th><th>份额</th><th>卖出净值</th><th>毛额</th><th>成本</th><th>赎回费</th><th>已实现</th></tr></thead><tbody>
      ${(RZ.sales||[]).map(s=>`<tr>
        <td>${esc(s.confirm_date||'—')}</td>
        <td>${esc(s.fund_name||s.fund_code||'')}</td>
        <td>${(Number(s.shares)||0).toFixed(2)}</td>
        <td>${fmt(s.nav)}</td>
        <td>${fmtMoney(s.gross)}</td>
        <td>${fmtMoney(s.cost)}</td>
        <td>${fmtMoney(s.fee)}</td>
        <td style="color:${(s.pnl||0)>=0?'var(--up)':'var(--down)'}">${fmtMoney(s.pnl)}</td></tr>`).join('')}
      </tbody></table></div></details>`;
  }
  h+=`<div class="qtag u-mt10">历史持仓成本按<b>旧口径</b>（确认日 T+1 净值）计算，仅新流水按<b>申请日 T 净值</b>定价。
    评估历史回填的影响：<code>python scripts/rebuild_costs_dryrun.py</code>（只读，不改数据）</div>`;
  return h;
}

function rebalanceInner(RB,P){
  return `<h2>调仓建议 <span class="sub">— 当前权益 vs 目标</span></h2>`+rebalanceHTML(RB,P);
}
// 局部刷新时复用同一套渲染（持仓卡内容）
function holdingsInner(P,PL){ return holdingsHTML(P,PL); }

function rebalanceHTML(RB,P){
  if(!RB||RB.error){return`<div class="empty">${RB&&RB.error?esc(RB.error):'调仓分析不可用'}</div>`;}
  if(!(P&&P.has_holdings))return`<div class="empty">暂无持仓，暂无需调仓</div>`;
  if(!RB.instructions||!RB.instructions.length)return`<div class="empty">当前无需调仓</div>`;
  const summary=RB.summary||{};
  const col=RB.need_rebalance?'var(--yellow)':'var(--green)';
  let h=`<div style="font-size:15px;font-weight:700;margin-bottom:6px;color:${col}">${esc(summary.verdict||'')}</div>
    <div class="stat-line">${esc(summary.detail||'')}</div>`;
  // 总资金口径缺现金弹药 → 仓位被高估，必须显式提示（不静默填 0）
  if((RB.degraded||[]).includes('cash_reserve')){
    h+=`<div class="alert alert-info" style="border-left-color:var(--warn)">
      <b>⚠️ 数据缺失：计划现金弹药</b><br>
      <span class="u-tdim">读不到 <code>plan.cash_reserve</code>，总资金口径按 0 计算 —— 权益占比被<b>高估</b>，上面的调仓金额仅供参考。</span></div>`;
  }
  for(const inst of RB.instructions.slice(0,6)){
    const icon={'卖出':'','买入':'','持有':''};
    const ac={'卖出':'var(--red)','买入':'var(--green)','持有':'var(--dim)'};
    h+=`<div class="alert alert-info" style="border-left-color:${ac[inst.action]||'var(--blue)'}">
      <b>${icon[inst.action]||'•'} [${esc(inst.action)}] ${esc(inst.fund_code)} ${esc((inst.fund_name||'').slice(0,26))}</b>
      <span style="float:right;font-weight:700;color:${ac[inst.action]||'var(--text)'}">${fmtMoney(inst.amount)}</span><br>
      <span class="u-tdim">${esc(inst.reason||'')}</span></div>`;
  }
  h+=`<div class="qtag u-mt8">操作前确认持有天数 — 不满7天有 1.5% 惩罚赎回费</div>`;
  return h;
}

function poolHTML(F){
  const funds=F.funds||[];
  let head=`<h2>基金质量筛选池 <span class="sub">— 不推荐“买哪只”，只排除有坑的</span>
      <span class="u-right">
        <button class="btn mini" data-action="showPool" data-mode="type">按类型</button>
        <button class="btn mini primary" data-action="showPool" data-mode="board">按板块总榜</button>
      </span></h2>`;
  if(!funds.length)return head+'<div class="empty">暂无基金数据 · 运行 python src/main.py nav && python src/main.py enrich</div>';
  const summary=F.summary||{};
  // 费率缺失时不得显示"均费率 0.00%"（会被读成零费率），改为显式声明缺失
  const feeLine=(summary.fee_n>0)?`均费率 ${fmt(summary.avg_fee,2)}%（${summary.fee_n} 只有数据）`
                                 :`<span class="qtag">费率数据缺失（${summary.total||0} 只均无费率）</span>`;
  let h=head+`<div class="stat-line">共 ${summary.total||0} 只 · ${feeLine}`+
    (summary.limited_n?` · <span class="u-twarn">限大额 ${summary.limited_n}</span>`:'')+
    (summary.status_unknown_n?` · <span class="qtag">申购状态未知 ${summary.status_unknown_n}</span>`:'')+
    `</div>`;
  // 口径说明（C1/C2 的可读性前提）：不解释清楚，"同类 P" 会被当成预测能力
  h+=`<div class="qtag" style="margin:6px 0 12px;line-height:1.7">口径：「同类 P__」= 该基金在<b>同组存续基金</b>中的百分位（0–100，越大越好，已按方向归一：回撤/波动越小=分越高）。`+
     `动量单独以「位置」展示 —— <b>越大只表示近 3 月涨得越多，不代表更好</b>。存续不足 3 年（样本不足）时不给百分位，只说明原因。</div>`;
  // C4：把此前从未被前端调用的 /api/recommend 接进来（可折叠 + 强口径声明），
  //     不让它继续当"死接口"，但也不把它渲染成"推荐榜"误导用户。
  h+=`<details class="recbox" ontoggle="loadRec(this)"><summary>历史回测验证（样本内 · 仅供参考，不是推荐）</summary>`+
     `<div class="recmain"><span class="qtag">展开后加载…</span></div></details>`;
  const groups={};
  for(const f of funds){const ft=f.type||'其他';(groups[ft]=groups[ft]||[]).push(f);}
  for(const g of Object.values(groups))g.sort((a,b)=>(b.risk||'').includes('稳健')-(a.risk||'').includes('稳健'));
  const sortedTypes=Object.keys(groups).sort((a,b)=>groups[b].length-groups[a].length);
  for(const bt of sortedTypes){
    const gfunds=groups[bt]; const show=gfunds.slice(0,6);
    const lab=show.length<gfunds.length?`共${gfunds.length}只 · 展示前${show.length}`:`共${gfunds.length}只`;
    h+=`<div class="groupbox"><div class="gh">${esc(bt)} <span class="sub">${lab}</span></div>`;
    for(const f of show)h+=poolRow(f);
    h+='</div>';
  }
  return h;
}
// 批次 C：同侪参照系展示
//   C1 三要素：同类 P__ · 组别 · N 只对照 · 截至 日期
//   C2 动量只作「位置」，**不用涨跌语义色**（否则被误读成"更好"）
//   C3 F 组降级措辞（与第三方同类 ρ 仅 0.52，A/B/C/D 为 0.94~0.99）
function peerLine(f){
  const g=f.group, n=f.group_n, asof=f.nav_asof;
  if(f.insufficient_data||!f.percentiles){
    return `<span class="qtag" title="不显示百分位的原因：${esc(f.reason||'样本不足')}">同类 P— · ${esc(f.reason||'数据不足')}</span>`;
  }
  const P=f.percentiles||{};
  const comp=(P.sharpe!=null&&P.max_drawdown_1y!=null)?Math.round(0.6*P.sharpe+0.4*P.max_drawdown_1y):null;
  const nm=(g==='F QDII/其他')
    ?` <span class="qtag u-twarn" title="F 组是 QDII/FOF/货币/商品/REITs 的大杂烩，与厂商同类口径的 ρ 仅 0.52（A/B/C/D 为 0.94~0.99）→ 可比性弱于其他组">同类为大类口径</span>`:'';
  return `<span class="qtag">同类 P<b>${comp!=null?comp:'—'}</b> · ${esc(g||'—')}${n?(' · '+Number(n).toLocaleString()+' 只对照'):''}${asof?(' · 截至 '+esc(asof)):''}</span>${nm}`;
}
function posChip(f){
  const P=(f.percentiles||{}).momentum_3m;
  if(P==null)return `<span class="poschip" title="无参照系数据，不显示位置">位置 —</span>`;
  const w=Math.max(3,Math.min(100,P));
  return `<span class="poschip" title="同类位置 P${Math.round(P)}：仅表示近3月涨跌幅在同类中的位置，越大=涨得越多，**不代表更好**（追涨警示另见风险标签）"><span class="posbar"><i style="width:${w}%"></i></span>位置 P${Math.round(P)}</span>`;
}
function qChips(f){
  const P=f.percentiles||{};
  const one=(k,lb)=>(P[k]==null)?'':`<span class="qchip">${lb} P${Math.round(P[k])}</span>`;
  return one('sharpe','夏普')+one('max_drawdown_1y','回撤')+one('annual_return','年化');
}
function poolRow(f){
  const navs=f.nav_trend||[]; const mom=f.momentum_3m||0; const dd=f.max_dd_1y||0;
  const trendC=mom>=0?'var(--green)':'var(--red)';
  const name=f.name||f.code;
  // 综合评分（类型桶内归一化，同风险等级内的排序依据；缺失显示 --）
  const sc=(f.score!==null&&f.score!==undefined&&f.score===f.score)?Math.round(f.score):null;
  const scoreTag=sc!==null?`<span class="qtag" title="综合评分：类型桶内归一化百分位">评分 ${sc}</span>`:'<span class="qtag" title="综合评分缺失">评分 --</span>';
  // 申购状态是独立一轴：限大额不影响质量等级，单独挂标签（不静默、也不误导）
  const ps=f.purchase_status||'';
  const psTag=ps?`<span class="pill u-pillwarn" title="${esc(f.purchasable||ps)}">${esc(ps)}</span>`:'';
  return `<div class="fund-row">
    <span><span class="fund-code">${esc(f.code)}</span>${riskBadge(f.risk)}</span>
    <span class="fund-name" title="${esc(name)}">${esc(name.length>26?name.slice(0,26)+'…':name)}${psTag}</span>
    <span class="fund-meta"><span class="sparkline">${sparkSVG(navs,trendC)}</span>${scoreTag}
      <span style="color:var(--ink-muted);min-width:62px;text-align:right">近3月 ${mom>=0?'+':''}${fmt(mom,1)}%</span>
      <span class="qtag">回撤 ${fmt(dd,0)}%</span></span>
    <span></span>
    <span class="peer-line">${peerLine(f)}</span>
    <span class="fund-meta">${posChip(f)}${qChips(f)}</span></div>`;
}
// C4：/api/recommend 的懒加载渲染（可折叠；口径声明必须与内容同屏）
let REC_LOADED=false;
async function loadRec(el){
  if(!el||!el.open||REC_LOADED)return;
  const box=el.querySelector('.recmain'); if(!box)return;
  box.innerHTML='<div class="loading"><span class="spinner"></span>正在跑历史回测（首次约 15-20 秒）...</div>';
  try{
    const r=await fetch('/api/recommend'); const j=await r.json();
    if(!j.ok)throw new Error(j.error||'fail');
    REC_LOADED=true;
    const d=j.data||{}; const picks=d.current_picks||[];
    let h=`<div class="qtag" style="color:var(--warn);line-height:1.7;margin:6px 0 8px">`+
      `口径：这是<b>样本内</b>结果 —— 用已实现的前向收益反筛"赢家"存在同义反复，`+
      `<b>不构成选基能力证据</b>；本项目 L1 基线（见 docs/recsys_ml_report.md）也未跑赢手工权重。`+
      `主推荐请回到上方温度驱动的实时筛选池。</div>`;
    h+=`<div class="stat-line">本期候选 ${picks.length} 只</div>`;
    for(const p of picks){
      const P=p.percentiles||{};
      const ok=!p.insufficient_data&&p.sharpe!==undefined;
      const comp=(P.sharpe!=null&&P.max_drawdown_1y!=null)?Math.round(0.6*P.sharpe+0.4*P.max_drawdown_1y):null;
      const tag=ok?`<span class="qtag">同类 P${comp} · ${esc(p.group||'—')}${p.group_n?(' · '+Number(p.group_n).toLocaleString()+' 只对照'):''}${p.nav_asof?(' · 截至 '+esc(p.nav_asof)):''}</span>`
                   :`<span class="qtag">同类 P— · ${esc(p.reason||'数据不足')}</span>`;
      h+=`<div class="fund-row" style="grid-template-columns:74px 1fr auto">
        <span class="fund-code">${esc(p.code||'')}</span>
        <span class="fund-name">${esc(p.name||p.code||'')}</span>
        <span class="fund-meta">${tag}</span></div>`;
    }
    box.innerHTML=h||'<div class="empty">无可展示的历史验证结果</div>';
  }catch(e){
    box.innerHTML='<div class="empty">历史回测加载失败 · '+esc(e.message||'')+'</div>';
  }
}
// 第 8 项：按板块总榜（每板块前 20，不折叠截断）
async function showPool(mode,_attempt){
  const el=document.getElementById('pool-card');
  if(!el)return;
  const attempt=_attempt||0;
  if(mode==='type'){ el.innerHTML=poolHTML((STATE.all&&STATE.all.funds)||{}); return; }
  el.innerHTML='<h2>基金质量筛选池 <span class="sub">— 按行业板块总榜</span></h2><div class="loading"><span class="spinner"></span>正在按板块聚合（首次较慢，之后走缓存）...</div>';
  try{
    const r=await fetch('/api/funds/board?size=20&limit=200'); const j=await r.json();
    if(j.status==='warming'){                      // 冷启动不阻塞（同 /api/sectors 模式）
      const wait=Math.max(5,Math.min(j.retry_in||15,30));
      el.innerHTML='<h2>基金质量筛选池 <span class="sub">— 按行业板块总榜</span></h2>'
        +'<div class="loading"><span class="spinner"></span>后台正在计算筛选池（约 1–2 分钟）· 第 '+(attempt+1)+' 次等待，'+wait+' 秒后重试</div>';
      if(attempt<10){ await sleep(wait*1000); return showPool(mode,attempt+1); }
      el.innerHTML='<h2>基金质量筛选池</h2><div class="empty">后台仍在计算，可稍后重试'+retryBtn("showPool('board')")+'</div>';
      return;
    }
    if(!j.ok)throw new Error(j.error||'fail');
    STATE.boardPool=j.data;
    el.innerHTML=boardPoolHTML(j.data);
  }catch(e){
    el.innerHTML='<h2>基金质量筛选池</h2><div class="empty">板块总榜加载失败 · '+esc(e.message||'')+retryBtn("showPool('board')")+'</div>';
  }
}
function boardPoolHTML(d){
  const boards=d.boards||[];
  let h=`<h2>基金质量筛选池 <span class="sub">— 按行业板块总榜（每板块前 ${d.size||20}）</span>
      <span class="u-right"><button class="btn mini" data-action="showPool" data-mode="type">按类型</button></span></h2>
    <div class="stat-line">共 ${d.total_funds||0} 只候选 · ${boards.length} 个板块 · 组内“稳健优先 → 夏普 → 动量”排序</div>`;
  if(!boards.length)return h+'<div class="empty">暂无可展示的板块分组</div>';
  boards.forEach((b,i)=>{
    h+=`<details class="groupbox" ${i<3?'open':''}><summary class="gh u-pointer">
        ${esc(b.board)} <span class="sub">共 ${b.total} 只${b.total>(d.size||20)?` · 展示前 ${d.size||20}`:' · 全部展示'}</span></summary>`;
    for(const f of b.funds)h+=poolRow(f);
    h+='</details>';
  });
  return h;
}

// ========== 市场信号（独立 /api/sentiment） ==========
function signalsInner(S){
  let h='';
  if(S.scanning){
    h='<div class="loading"><span class="spinner"></span>后台联网扫描宏观/基金信号中…</div>';
    if(S.slow)h+='<div class="qtag u-center">耗时较长，可稍后点 刷新</div>';
    return h;
  }
  if(S.error)return'<div class="empty">信号加载失败：'+esc(S.error)+retryBtn('loadSentiment()')+'</div>';
  if(S.all_clear)return'<div class="status"><span class="dot dot-green"></span>本周无需要关注的信号</div>';
  let hh=`<div class="stat-line">${esc(S.signal_summary||'')}</div>`;
  for(const a of(S.alerts||[]).slice(0,5)){
    const cls=a.level==='🔴'?'alert-red':(a.level==='🟡'?'alert-yellow':'alert-info');
    hh+=`<div class="alert ${cls}"><b>[${esc(a.category||'')}]</b> ${esc(a.title||'')}<br><span class="u-tdim">${esc(a.detail||'')}</span></div>`;
  }
  hh+='<div class="qtag" style="margin-top:9px">只显示能影响决策的信号</div>';
  return hh;
}
async function loadSentiment(){
  const card=document.getElementById('sentiment-card');
  if(!card)return; card.dataset.done='1';
  for(let i=0;i<9;i++){
    try{
      const res=await fetch('/api/sentiment'); const j=await res.json();
      if(j.status==='scanning'){card.innerHTML=signalsInner({scanning:true,slow:i>=7});await sleep((j.retry_in||25)*1000);continue;}
      if(j.ok){card.innerHTML=signalsInner(j.data||{all_clear:true});return;}
      throw new Error(j.error||'加载失败');
    }catch(e){card.innerHTML=signalsInner({error:(e.message||'加载失败')});return;}
  }
}

// ========== 板块（独立 /api/sectors） ==========
async function loadSectors(retries=5){
  const card=document.getElementById('sector-card');
  if(!card)return;
  for(let attempt=0;attempt<retries;attempt++){
    try{
      const r=await fetch('/api/sectors'); const j=await r.json();
      // 板块改成后台计算了：冷启动会返回 warming，按服务端给的间隔回来轮询（不报错）
      if(j.status==='warming'){
        card.innerHTML='<div class="loading"><span class="spinner"></span>板块数据后台计算中… ('+(attempt+1)+'/'+retries+')</div>';
        await sleep((j.retry_in||8)*1000); continue;
      }
      if(!j.ok)throw new Error(j.error||'fail');
      const sectors=(j.data&&j.data.sectors)||[]; if(!sectors.length)throw new Error('empty');
      const momentum=j.data.momentum_leaders||[], value=j.data.value_candidates||[];
      const age=j.data.cached_at||'';
      // 第 9 项：Top 10 → Top 20；靠后 5 → 靠后 10
      const topN=sectors.slice(0,20), bottomN=sectors.slice(-10);
      let h=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:22px">
        <div><div style="font-size:13px;font-weight:700;color:var(--green);margin-bottom:8px">综合排名 Top 20</div>`;
      for(const s of topN){
        const barW=Math.max(4,(s.score+1.5)*40); const barC=s.score>0.5?'var(--green)':s.score>0?'var(--muted)':'var(--yellow)';
        const momC=(s.ret_1m||0)>0?'var(--green)':'var(--red)';
        h+=`<div class="fund-row" style="grid-template-columns:26px 1fr auto">
          <span class="u-tmuted">#${s.rank}</span><span class="fund-name">${esc(s.name)}</span>
          <span class="fund-meta"><span style="width:${barW}px;height:4px;background:${barC};border-radius:2px;display:inline-block"></span>
          <span style="color:${momC};min-width:48px;font-weight:600">${(s.ret_1m||0)>=0?'+':''}${pv(s.ret_1m,1)}%</span></span></div>`;
      }
      h+=`</div><div><div style="font-size:13px;font-weight:700;color:var(--red);margin-bottom:8px">排名靠后 10</div>`;
      for(const s of bottomN){
        const momC=(s.ret_1m||0)>0?'var(--green)':'var(--red)';
        h+=`<div class="fund-row" style="grid-template-columns:26px 1fr auto"><span class="u-tmuted">#${s.rank}</span>
          <span class="fund-name">${esc(s.name)}</span><span class="fund-meta"><span style="color:${momC};font-weight:600">${(s.ret_1m||0)>=0?'+':''}${pv(s.ret_1m,1)}%</span>
          <span class="qtag">波${pv(s.volatility,0)}%</span></span></div>`;
      }
      if(momentum.length){h+=`<div class="u-subhead">动量领涨</div>`;for(const s of momentum.slice(0,3)){h+=`<div class="fund-row" style="grid-template-columns:1fr auto"><span class="fund-name">${esc(s.name)}</span><span class="fund-meta"><span class="u-tgreen">近1月${(s.ret_1m||0)>=0?'+':''}${pv(s.ret_1m,1)}%</span><span class="qtag">3月${(s.ret_3m||0)>=0?'+':''}${pv(s.ret_3m,0)}%</span></span></div>`;}}
      if(value.length){h+=`<div class="u-subhead">超跌候选（逆向）</div>`;for(const s of value.slice(0,3)){h+=`<div class="fund-row" style="grid-template-columns:1fr auto"><span class="fund-name">${esc(s.name)}</span><span class="fund-meta"><span class="u-tred">近1月${(s.ret_1m||0)>=0?'+':''}${pv(s.ret_1m,1)}%</span><span class="qtag">回撤${pv(s.max_dd,0)}%</span></span></div>`;}}
      h+=`</div></div><div class="qtag u-mt10">评分 = 动量40% + 趋势30% + 风险调整30%${age?' · 缓存于 '+esc(age):''}</div>`;
      card.innerHTML=h; card.dataset.done='1'; return;
    }catch(e){
      if(attempt<retries-1){card.innerHTML='<div class="loading"><span class="spinner"></span>等待数据就绪... ('+(attempt+2)+'/'+retries+')</div>';await sleep(3000);}
      else{card.innerHTML='<div class="empty">板块数据加载失败'+retryBtn("loadSectors(true)")+'</div>';card.dataset.done='1';}
    }
  }
}

// ========== 量化模型（独立 /api/quant_models） ==========
function qTable(rows,cols){let h='<table class="tbl"><thead><tr>';cols.forEach(c=>h+='<th>'+c.label+'</th>');h+='</tr></thead><tbody>';
  rows.forEach(r=>{h+='<tr'+(r.best?' class="best"':'')+'>';cols.forEach(c=>h+='<td>'+c.html(r)+'</td>');h+='</tr>';});h+='</tbody></table>';return h;}
function volHTML(list){
  if(!list||!list.length)return'<div class="empty">vol 对比数据缺失</div>';
  const BASE={'EWMA':1,'6M_Hist':1,'HAR-RV':1};
  const rows=list.map(r=>({r}));let best=null,bq=null;
  for(const o of rows)if(!BASE[o.r.model]&&(best==null||Number(o.r.ic_mean)>Number(best.r.ic_mean)))best=o;
  for(const o of rows)if(!BASE[o.r.model]&&(bq==null||Number(o.r.qlike)<Number(bq.r.qlike)))bq=o;
  if(best)best.best=true;
  const cols=[{label:'模型',html:o=>`<span class="pill ${BASE[o.r.model]?'':'ml'}">${BASE[o.r.model]?'基线':'ML'}</span>${esc(o.r.model)}`},
    {label:'IC',html:o=>`<b>${pv(o.r.ic_mean,3)}</b>`},{label:'ICIR',html:o=>pv(o.r.icir,1)},
    {label:'QLIKE↓',html:o=>(bq&&bq.r.model===o.r.model)?`<span class="sig">${pv(o.r.qlike,3)}</span>`:pv(o.r.qlike,3)},
    {label:'MZ_β',html:o=>pv(o.r.mz_beta,2)},{label:'DM_p',html:o=>pCell(o.r.dm_p_vs_base)},{label:'排列_p',html:o=>pCell(o.r.perm_p_value)}];
  return qTable(rows,cols);
}
function ddHTML(list){
  if(!list||!list.length)return'<div class="empty">回撤预警数据缺失</div>';
  const best={};for(const r of list){const t=Number(r.threshold);if(!best[t]||Number(r.auc)>Number(best[t].auc))best[t]=r;}
  const rows=Object.keys(best).sort((a,b)=>a-b).map(t=>({r:best[t],best:true}));
  const M={'History_Freq':'历史频率','RandomForest':'随机森林'};
  const cols=[{label:'阈值',html:o=>`≥${pv(o.r.threshold,0)}%`},{label:'最优模型',html:o=>esc(M[o.r.model]||o.r.model)},
    {label:'AUC',html:o=>`<b>${pv(o.r.auc,3)}</b>`},{label:'召回',html:o=>`<span class="sig">${pct(o.r.recall_pos,1)}</span>`},
    {label:'F1',html:o=>pct(o.r.f1,1)},{label:'漏报',html:o=>`<span class="${o.r.miss_rate>25?'neg':''}">${pct(o.r.miss_rate,1)}</span>`}];
  return qTable(rows,cols);
}
function portHTML(list){
  if(!list||!list.length)return'<div class="empty">组合模拟数据缺失</div>';
  const name={'Equal_Weight':'等权基准','Vol_Targeting':'Vol-Targeting','Vol_DD_Combined':'回撤叠加(Vol+DD)'};
  const cols=[{label:'策略',html:o=>esc(name[o.r.scheme]||o.r.scheme)},
    {label:'年化收益',html:o=>`<span class="${o.r.annual_return>=0?'pos':'neg'}">${pct(o.r.annual_return,2)}</span>`},
    {label:'年化波动',html:o=>pct(o.r.annual_volatility,1)},
    {label:'最大回撤',html:o=>`<span class="${o.r.max_drawdown>24?'neg':''}">${pct(o.r.max_drawdown,1)}</span>`},
    {label:'夏普',html:o=>pv(o.r.sharpe,2)},{label:'Calmar',html:o=>pv(o.r.calmar,2)}];
  return qTable(list.map(r=>({r})),cols);
}
function chartHTML(rows){
  if(!rows||rows.length<2)return'<div class="empty">无月度仓位序列</div>';
  const W=760,H=170,l=8,r=8,t=14,b=22,n=rows.length;
  const x=i=>l+i*(W-l-r)/(n-1);
  const y=v=>t+(100-Math.max(0,Math.min(100,v==null?0:v)))/100*(H-t-b);
  const grid=[0,25,50,75,100].map(v=>`<line x1="${l}" y1="${y(v)}" x2="${W-r}" y2="${y(v)}" stroke="var(--hairline)" stroke-width="1"/>`).join('');
  const line=(key,color)=>{let d='';let started=false;for(let i=0;i<rows.length;i++){const v=rows[i][key];if(v==null){started=false;continue;}d+=(started?' ':'')+x(i).toFixed(1)+','+y(v).toFixed(1);started=true;}return`<polyline points="${d}" fill="none" stroke="${color}" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>`;};
  const trig=rows.map((s,i)=>s.dd_triggered?`<circle cx="${x(i)}" cy="${y(Math.max(Number(s.combined_pos)||0,0))-6}" r="2.8" fill="var(--canvas)" stroke="var(--down)" stroke-width="1.6"/>`:'').join('');
  const step=Math.ceil(n/6);
  const lab=rows.map((s,i)=>i%step===0?`<text x="${x(i)}" y="${H-6}" font-size="9" fill="var(--ink-tertiary)" text-anchor="${i===0?'start':i===n-1?'end':'middle'}">${s.month.replace('-','/')}</text>`:'').join('');
  return`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="月度仓位">${grid}${line('vol_target_pos','var(--primary)')}${line('combined_pos','var(--ink-subtle)')}${trig}${lab}</svg>`;
}
async function loadQuantModels(retries=2){
  const card=document.getElementById('quant-card');
  if(!card)return;
  for(let attempt=0;attempt<retries;attempt++){
    try{
      const res=await fetch('/api/quant_models'); const j=await res.json();
      if(!j.ok)throw new Error(j.error||'fail');
      const d=j.data||{};
      const volV=volHTML(d.vol||[]),ddV=ddHTML(d.drawdown||[]),portV=portHTML(d.portfolio||[]),chartV=chartHTML(d.signals||[]);
      const empty=!(d.vol||[]).length&&!(d.drawdown||[]).length&&!(d.portfolio||[]).length&&!(d.signals||[]).length;
      const rn={'vol_model_comparison_report.md':'波动率多模型','drawdown_warning_report.md':'回撤预警','portfolio_simulation_report.md':'组合模拟'};
      let foot='';
      if(d.reports){const have=Object.keys(d.reports).filter(k=>d.reports[k]).map(k=>rn[k]||k);if(have.length)foot='可查看离线报告：'+have.map(x=>`<code>${esc(x)}</code>`).join(' · ');}
      let html='';
      if(empty)html='<div class="empty">暂无可展示的量化模型结果 · 请先运行 vol_model_comparison / drawdown_warning / portfolio_simulation</div>';
      else{
        // 第 10 项：先给“结论行”，细节折叠，提高信息密度与可读性
        const BASEV={'EWMA':1,'6M_Hist':1,'HAR-RV':1};
        const vol=(d.vol||[]).slice().sort((a,b)=>b.ic_mean-a.ic_mean);
        const ml=vol.filter(v=>!BASEV[v.model]);
        const baseV=vol.find(v=>v.model==='HAR-RV')||vol[0];
        const bestMl=ml[0];
        let concl='';
        if(bestMl){
          const sig=(bestMl.perm_p_value!=null&&bestMl.perm_p_value<0.05);
          concl+=`<span class="pill ml">vol 最优 ${esc(bestMl.model)}</span> IC ${pv(bestMl.ic_mean,3)} vs 基线 ${esc(baseV?baseV.model:'-')} ${pv(baseV&&baseV.ic_mean,3)}
            · QLIKE ${pv(bestMl.qlike,3)} · 排列检验 ${bestMl.perm_p_value==null?'—':(sig?'p='+pv(bestMl.perm_p_value,3)+' 显著':'p='+pv(bestMl.perm_p_value,3)+' 不显著')}`;
        }
        const dd5=(d.drawdown||[]).filter(r=>Number(r.threshold)===5);
        if(dd5.length){
          const b=dd5.slice().sort((a,b)=>b.auc-a.auc)[0];
          concl+=`　<span class="pill">回撤预警 ≥5%</span> ${esc(b.model)} AUC ${pv(b.auc,3)} · 召回 ${pct(b.recall_pos,1)}`;
        }
        const ports=(d.portfolio||[]);
        if(ports.length){
          const vt=ports.find(p=>p.scheme==='Vol_Targeting');
          if(vt)concl+=`　<span class="pill">组合</span> Vol-Targeting 年化 ${pct(vt.annual_return,2)} · 回撤 ${pct(vt.max_drawdown,1)} · 夏普 ${pv(vt.sharpe,2)}`;
        }
        if(concl)html+=`<div class="conclusion">${concl}</div>`;
        html+=`<div class="quant-grid">
          <details class="qsec" open><summary><h3 class="u-inline">波动率预测 · 多模型对比</h3></summary>
            <div class="cap">排名 IC · QLIKE 越低越好 · MZ_β 越接近1越“诚实”</div>${volV}</details>
          <div>
            <details class="qsec" open><summary><h3 class="u-inline">回撤预警 · 各阈值最优模型</h3></summary>
              <div class="cap">更看重“正类召回”（漏报代价更高）</div>${ddV}
              <div class="note">建议以 ≥5% 阈值模型实盘。</div></details>
            <details class="qsec" open><summary><h3 class="u-inline">组合模拟</h3></summary>
              <div class="cap">Vol-Targeting 目标年化波动 15% · 月再平衡</div>${portV}
              <div class="note">回撤叠加未降回撤反损收益——组合层面以 <b class="u-tblue">Vol-Targeting</b> 为准。</div></details>
          </div></div>
          <details class="qsec u-mt18"><summary><h3 class="u-inline">月度仓位信号 · 回撤预警叠加前后对比</h3></summary>${chartV}
            <div class="qlegend"><span class="k"><span class="sw u-bgprimary"></span>Vol-Targeting</span>
              <span class="k"><span class="sw u-bgsub"></span>回撤叠加后</span>
              <span class="k"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;border:1.5px solid var(--down)"></span>触发预警月</span></div></details>`;
      }
      html+=`<div class="qtag" style="margin-top:14px"><span class="pill ml">OOS 2023-2026</span> ${foot}</div>`;
      card.innerHTML=html; card.dataset.done='1'; return;
    }catch(e){
      if(attempt<retries-1){await sleep(800);}else{card.innerHTML='<div class="empty">量化模型结果加载失败 · '+esc(e.message||'')+retryBtn('loadQuantModels()')+'</div>';card.dataset.done='1';}
    }
  }
}

function setBusy(b){ document.body.classList.toggle('busy', b); }
function secHead(t,s){return`<div class="sec-head"><div><div class="t">${t}</div><div class="s">${s||''}</div></div></div>`;}

// ========== 首屏：轻量总览先出（不等筛选/调仓） ==========
// 总览 section 的**单一实现**（2026-09-22 消除双渲染路径）：
// 以前 renderApp（全量）与 overviewViewHTML（快速首屏）各拼一份总览，改一处漏一处。
// 现在两条路径都调这里。
function overviewSectionHTML(T,P,PL,S,C){
  return `<section class="view active" data-view="overview">${secHead('总览','总资产 · 累计收益 · 市场温度 · 目标仓位 · 市场信号')}
    ${overviewKpis(T,P,PL,S)}
    <div class="grid">${curveCard(C)}${ovTempCard(T)}${ovAllocCard(T,P,PL)}${card('市场信号','LPR / PMI / 基金公告','<div id="sentiment-card"><div class="loading"><span class="spinner"></span>扫描中...</div></div>')}
      ${allocCard(S)}
    </div></section>`;
}
function overviewViewHTML(T,P,PL,S,C){
  return overviewSectionHTML(T,P,PL,S,C);
}

async function loadOverviewFast(fresh){
  try{
    const r=await fetch('/api/overview'+(fresh?'?fresh=1':''));
    const j=await r.json();
    if(!j.ok)throw new Error(j.error||'fail');
    document.getElementById('app').innerHTML=overviewViewHTML(j.data.temp||{},j.data.portfolio||{},j.data.plan||{},j.data.stats||{},j.data.curve||{});
    showView('overview');
    const sc=document.getElementById('sentiment-card'); if(sc&&!sc.dataset.done)loadSentiment();
    return true;
  }catch(e){ return false; }
}
async function boot(){
  const app=document.getElementById('app');
  // 数据有写入(加仓/减仓/定投)或点了“重算”时强制 fresh 重算
  const fresh=sessionStorage.getItem('dash_dirty')==='1';
  if(fresh)sessionStorage.removeItem('dash_dirty');
  setBusy(true);
  app.innerHTML='<div class="loading"><span class="spinner"></span>正在加载总览…</div>';
  // 阶段1：轻量总览（约2~3s）先渲染，尽快给用户内容
  await loadOverviewFast(fresh);
  // 阶段2：后台补齐完整数据（筛选池/调仓等），就绪后切到完整布局并保持当前视图
    try{
      const r=await fetch('/api/all'+(fresh?'?fresh=1':''));
      const j=await r.json();
      if(j.status==='warming'){
        // 冷启动（筛选池 ~110s）：阶段1 的总览已经能用，这里延后自动补齐完整数据
        const wait=Math.max(5,Math.min(j.retry_in||15,30));
        setTimeout(()=>{ loadAll(false).catch(()=>{}); }, wait*1000);
      } else if(j.ok){ STATE.all=j.data; ready=true; renderApp(); }
    }catch(e){ /* 总览仍可用 */ }
  // 幂等对账：结算到期的待确认买卖（写路径从 GET 剥离后，这是页面级的显式触发点）。
  // 只有真的结算了东西才局部刷新，绝不打断用户当前所在的视图。
  try{
    const rc=await postJSON('/api/reconcile',{});
    if(rc&&rc.ok&&rc.changed)await reloadHoldings(true);
  }catch(e){ /* 对账失败不影响浏览 */ }
  setBusy(false);
  const tu=document.getElementById('updateTime'); if(tu)tu.textContent='更新于 '+new Date().toLocaleTimeString('zh-CN');
}

boot();
