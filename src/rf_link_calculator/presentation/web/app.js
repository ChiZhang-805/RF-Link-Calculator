'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy = value => structuredClone(value);
const icon = name => `<svg class="ico" aria-hidden="true"><use href="/icons.svg#${name}"></use></svg>`;
const button = (name, action, label = '', extra = '') => `<button type="button" class="${label ? '' : 'icon ghost'}" data-action="${action}" aria-label="${esc(label || actionNames[action] || action)}" title="${esc(label || actionNames[action] || action)}" ${extra}>${icon(name)}${label ? `<span>${esc(label)}</span>` : ''}</button>`;
const actionNames = {new:'新建项目',open:'打开项目',save:'保存项目',undo:'撤销',redo:'重做',settings:'设置',copy:'复制器件',delete:'删除器件',up:'上移',down:'下移',fit:'适应画布','zoom-in':'放大','zoom-out':'缩小','previous':'上一页','next':'下一页','close':'关闭','inspector':'器件参数','issues':'检查结果','project-edit':'项目属性'};
const unitScales = {Hz:1,kHz:1e3,MHz:1e6,GHz:1e9};
const state = {project:null,result:null,computed:null,revision:0,selected:null,view:'chain',inspectorTab:'parameters',inspectorOpen:false,page:0,analysisTab:'overview',analysisPage:0,analysisCapacity:10,diagramPage:0,zoom:1,pan:{x:0,y:0},history:[],future:[],busy:false,frequencyUnit:'MHz',bandwidthUnit:'MHz',directory:'',scan:null,modal:null,strict:false,metricsPage:0};
let config, resizeObserver, toastTimer, graphDrag;
const currentStage = () => state.project.stages.find(s => s.id === state.selected);
const fingerprint = () => JSON.stringify(state.project);
const stale = () => !state.result || state.computed !== fingerprint();
const num = (value, places = 2) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(places) : '—';
const metricNumber = (m, places=2) => m?.value==null && m?.status==='ideal' ? '理想' : num(m?.value,places);
const stagePowerText = (s,key) => s[key].mode==='ideal' ? '∞' : num(inputPower(s,key));
const compactNumber = value => value == null ? '' : Number(Number(value).toPrecision(10)).toString();
const options = (mapping, value) => Object.entries(mapping).map(([key,label]) => `<option value="${esc(key)}" ${String(key) === String(value) ? 'selected' : ''}>${esc(label)}</option>`).join('');
let storedKey = 'rf-link-workbench-v1';

function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.className = 'show' + (error ? ' error' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => {el.className = '';}, error ? 6500 : 3200);
}

async function api(action, data, binary = false) {
  const response = await fetch('/api/' + action, {method:'POST',headers:{'Content-Type':'application/json',...(config?.account?{'X-CSRF-Token':config.account.csrf}:{})},body:JSON.stringify(data)});
  if(response.status===401){sessionStorage.removeItem(storedKey);location.assign('/');throw new Error('请重新登录');}
  if (!response.ok) { const body = await response.json(); throw new Error(body.error || '操作未完成'); }
  return binary ? response : response.json();
}

function persist() {
  try { sessionStorage.setItem(storedKey, JSON.stringify({project:state.project,selected:state.selected,directory:state.directory,frequencyUnit:state.frequencyUnit,bandwidthUnit:state.bandwidthUnit})); } catch (_) { /* Full storage does not block editing or explicit saving. */ }
}

function checkpoint() {
  state.history.push(copy(state.project));
  if (state.history.length > 50) state.history.shift();
  state.future = [];
}

function changed({inspector = false, conditions = false, table = true} = {}) {
  state.revision++; state.scan = null; persist();
  refresh({inspector, conditions, table});
}

function setPath(object, path, value) {
  const parts = path.split('.'); let target = object;
  for (const part of parts.slice(0,-1)) { if (target[part] == null) target[part] = {}; target = target[part]; }
  target[parts.at(-1)] = value;
}

function inputBox(value, unit = '', attributes = '', type = 'number') {
  return `<div class="value-box"><input type="${type}" ${type === 'number' ? 'step="any"' : ''} value="${esc(value ?? '')}" ${attributes}>${unit ? `<span>${esc(unit)}</span>` : ''}</div>`;
}

function field(label, control, extra = '') {
  return `<div class="field-row ${extra}"><label>${esc(label)}</label>${control}</div>`;
}

function stageInput(label, path, value, unit = '', extra = '') {
  const binding=modelBinding(currentStage(),{gain_db:'gain_db','noise.value_db':'nf','ip3.value_dbm':'iip3','p1db.value_dbm':'ip1'}[path]);
  if(binding){value=binding.value;extra+=' disabled';}
  return field(label, inputBox(value,unit,`data-stage-field="${path}" aria-label="${esc(label)}" ${extra}`));
}

function stageSelect(label, path, mapping, value, extra = '') {
  const key=path.startsWith('ip3.')?'iip3':path.startsWith('p1db.')?'ip1':null,binding=key?modelBinding(currentStage(),key):null;
  if(binding){extra+=' disabled';value=path.endsWith('.reference')?'input':binding.value==null?'unknown':'finite';}
  return field(label,`<select data-stage-field="${path}" aria-label="${esc(label)}" ${extra}>${options(mapping,value)}</select>`);
}

function fitWorkbench() {
  const app = $('#app');
  const desktop = window.innerWidth >= 1100;
  const scale = desktop ? Math.min(1, window.innerWidth / 1440, window.innerHeight / 900) : 1;
  app.classList.toggle('desktop-fit', desktop);
  app.style.zoom = String(scale);
  app.style.width = desktop ? `${window.innerWidth / scale}px` : '';
  app.style.height = desktop ? `${window.innerHeight / scale}px` : '';
}

function mount() {
  fitWorkbench();
  $('#app').innerHTML = `
    <header class="topbar">
      <div class="brand">${icon('wave')}<span>RF Link</span></div>
      <button class="icon ghost project-pick" data-action="project-picker" title="切换链路" aria-label="切换链路">${icon('links')}</button>
      <nav class="main-nav" aria-label="工作区"><button data-view="chain" class="active">链路</button><button data-view="analysis">分析</button><button data-view="diagram">框图</button></nav>
      ${button('gear','settings')}
    </header>
    <div class="command-row">
      <section class="conditions panel" id="conditions" aria-label="分析条件"></section>
      <div class="top-actions panel" role="toolbar" aria-label="项目操作">
        ${button('new','new')}${button('folder','open')}${button('save','save')}
        <span class="divider optional-action"></span><span class="optional-action">${button('undo','undo')}</span><span class="optional-action">${button('redo','redo')}</span>
        <button class="primary" data-action="calculate" aria-label="计算" title="计算 · Ctrl+Enter">${icon('play')}<span>计算</span></button>
        <button class="export-btn" data-action="export" aria-label="导出">${icon('export')}<span>导出</span></button>
      </div>
    </div>
    <main class="workspace" id="workspace"></main>
    <section class="metrics panel" id="metrics" aria-label="关键结果"></section>`;
  $('#app').setAttribute('aria-busy','false');
  renderConditions(); renderWorkspace(); updateChrome(); renderMetrics();
}

function conditionField(label, key, value, unit, selector) {
  return `<div class="condition-field"><label for="condition-${key}">${label}</label><div class="value-box"><input id="condition-${key}" aria-label="${label}" type="number" step="any" value="${esc(compactNumber(value))}" data-condition="${key}">${selector ? `<select data-unit="${selector}" aria-label="${label}单位">${options(Object.fromEntries(Object.keys(unitScales).map(k=>[k,k])),unit)}</select>` : `<span>${unit}</span>`}</div></div>`;
}

function renderConditions() {
  const a = state.project.analysis;
  $('#conditions').innerHTML = conditionField('输入功率','input_power_dbm',a.input_power_dbm,'dBm') +
    conditionField('工作频率','source_frequency_hz',a.source_frequency_hz / unitScales[state.frequencyUnit],state.frequencyUnit,'frequencyUnit') +
    conditionField('噪声带宽','noise_bandwidth_hz',a.noise_bandwidth_hz / unitScales[state.bandwidthUnit],state.bandwidthUnit,'bandwidthUnit') +
    conditionField('信源温度','source_noise_temperature_k',a.source_noise_temperature_k,'K') +
    `<div class="segmented" aria-label="双音分析"><button data-tone="false" aria-pressed="${!a.two_tone.enabled}" class="${!a.two_tone.enabled?'active':''}" title="仅主CW分析">单音</button><button data-tone="true" aria-pressed="${a.two_tone.enabled}" class="${a.two_tone.enabled?'active':''}" title="主CW分析及独立双音分析">双音</button></div>` +
    `<button class="conditions-button" data-action="conditions">${icon('sliders')}<span>条件</span></button>`;
}

function updateChrome() {
  $('[data-action="project-picker"]').title = `${state.project.link_name || '未命名链路'} · 切换链路`;
  const circuitView=state.view==='analysis'&&state.analysisTab==='circuit';
  const dirty = circuitView?!labResult('circuit'):stale();
  const calculate = $('[data-action="calculate"]');
  calculate.classList.toggle('busy',state.busy);
  calculate.title = dirty ? '输入已更改 · Ctrl+Enter计算' : '计算 · Ctrl+Enter';
  calculate.disabled = state.busy;
  $('[data-action="export"]').disabled = (circuitView?!circuitSpec():dirty) || state.busy || labUI.busy;
  $('[data-action="undo"]').disabled = !state.history.length;
  $('[data-action="redo"]').disabled = !state.future.length;
  $$('[data-view]').forEach(el => {el.classList.toggle('active',el.dataset.view === state.view); el.setAttribute('aria-current',el.dataset.view === state.view ? 'page' : 'false');});
  document.title = `${state.project.link_name || '射频链路'} · RF Link`;
}

function zoomToolbar() {
  return `<div class="diagram-toolbar">${button('fit','fit')}<span class="zoom-level">${Math.round(state.zoom*100)}%</span>${button('zoom-in','zoom-in')}${button('zoom-out','zoom-out')}</div>`;
}

function issuesButton() {
  const count = stale() ? 0 : state.result.issues.length;
  return count ? `<button class="ghost issue-button" data-action="issues" title="查看${count}项检查" aria-label="检查结果">${icon('alert')}<span class="badge">${count}</span></button>` : '';
}

function renderWorkspace() {
  $('#app').classList.toggle('chain-mode',state.view==='chain');
  $('#app').classList.toggle('circuit-mode',state.view==='analysis'&&state.analysisTab==='circuit');
  // Preserve the result strip while switching views and rebuilding the workspace.
  const metrics = $('#metrics');
  $('#app').append(metrics);
  resizeObserver?.disconnect();
  const target = $('#workspace');
  if (state.view === 'chain') {
    target.innerHTML = `<section class="main-stack">
      <section class="panel chain-panel"><div class="panel-head"><h2>链路</h2><span class="count">${state.project.stages.length}</span><span class="spacer"></span><span id="issues-control">${issuesButton()}</span>${zoomToolbar()}</div><div class="diagram-viewport" id="chain-viewport"></div></section>
      <section class="panel table-panel"><div class="panel-head table-toolbar"><h2>器件</h2><span class="spacer"></span><button class="icon ghost inspector-toggle" data-action="inspector" title="器件参数" aria-label="器件参数">${icon('sliders')}</button><button class="add-button" data-action="add">${icon('plus')}<span>添加</span></button></div><div class="table-viewport" id="stage-table"></div><div class="pagination" id="table-pagination"></div></section>
    </section><button class="inspector-overlay ${state.inspectorOpen?'open':''}" data-action="close-inspector" aria-label="关闭器件参数"></button><aside class="panel inspector ${state.inspectorOpen?'open':''}" id="inspector" aria-label="器件参数"></aside>`;
    $('.main-stack',target).append(metrics);
    renderStageTable(); renderInspector(); renderChain();
  } else if (state.view === 'analysis') {
    target.innerHTML = `<section class="panel analysis-panel view-fill"><div class="panel-head"><h2>分析</h2><nav class="analysis-tabs" aria-label="分析视图">${[['overview','总览'],['stages','逐级'],['compression','压缩'],['tone','双音'],...labTabs].map(([key,label])=>`<button data-analysis="${key}" class="${state.analysisTab===key?'active':''}">${label}</button>`).join('')}</nav><span class="spacer"></span>${issuesButton()}<button class="icon ghost" data-action="all-metrics" title="全部指标" aria-label="全部指标">${icon('table')}</button></div><div class="analysis-content" id="analysis-content"></div></section>`;
    renderAnalysis();
  } else {
    const pages = Math.max(1,Math.ceil(state.project.stages.length / 8));
    state.diagramPage = Math.min(state.diagramPage,pages-1);
    target.innerHTML = `<section class="panel diagram-panel view-fill"><div class="panel-head"><h2>框图</h2><select class="panel-page-select" data-diagram-page aria-label="框图分段">${Array.from({length:pages},(_,i)=>`<option value="${i}" ${i===state.diagramPage?'selected':''}>${i+1} / ${pages}${pages>1?' · '+(i*8+1)+'–'+Math.min((i+1)*8,state.project.stages.length):''}</option>`).join('')}</select><span class="spacer"></span>${zoomToolbar()}<button data-action="export">${icon('export')}<span>导出</span></button></div><div class="diagram-viewport" id="chain-viewport"></div></section>`;
    renderChain();
  }
  resizeObserver = new ResizeObserver(() => {if (state.view==='analysis') fitAnalysisPage();});
  const analysis = $('#analysis-content'); if(analysis) resizeObserver.observe(analysis);
  updateStructureButtons();updateChrome();
}

function refresh({inspector = true,conditions = false,table = true} = {}) {
  if (!currentStage() && state.project.stages.length) state.selected = state.project.stages[0].id;
  if (conditions) renderConditions();
  updateChrome(); renderMetrics();
  if(state.view==='chain') {
    if(table) renderStageTable();
    if(inspector) renderInspector();
    else if($('#inspector h2') && currentStage()) {$('#inspector h2').textContent=currentStage().name;$('#inspector h2').title=currentStage().name;}
    renderChain();
    const count = $('.chain-panel .count'); if(count) count.textContent = state.project.stages.length;
    const issue = $('#issues-control'); if(issue) issue.innerHTML = issuesButton();
    updateStructureButtons();
  } else if(state.view==='analysis') renderAnalysis(); else renderWorkspace();
}

function actualNF(s) {
  if(s.noise.mode==='ideal') return 0;
  if(s.noise.mode==='manual') return s.noise.value_db;
  if(s.noise.mode==='passive_thermal' && s.gain_db!=null && s.gain_db<=0) return 10*Math.log10(1+(10**(-s.gain_db/10)-1)*s.physical_temperature_k/290);
  return null;
}

function inputPower(s, key) {
  const spec = s[key]; if(spec.mode!=='finite' || spec.value_dbm==null) return null;
  if(spec.reference==='input') return spec.value_dbm;
  if(s.gain_db==null) return null;
  return spec.value_dbm-s.gain_db+(spec.reference==='actual_output'?1:0);
}

function effectivePageSize() {return 5;}

function renderStageTable() {
  const target = $('#stage-table'); if(!target) return;
  const stages = state.project.stages, size=effectivePageSize();
  const pages = Math.max(1,Math.ceil(stages.length/size)); state.page=Math.min(state.page,pages-1);
  if(!stages.length) target.innerHTML = `<div class="empty"><button data-action="add">${icon('plus')}添加器件</button></div>`;
  else target.innerHTML = `<table class="stage-table" aria-label="器件表"><colgroup><col class="drag-col"><col class="check-col"><col class="name-col"><col class="type-col"><col><col class="metric-col"><col class="metric-col"><col class="metric-col"><col class="actions-col"></colgroup><thead><tr><th aria-label="排序"></th><th>启用</th><th class="name">器件</th><th class="type">类型</th><th>增益 / dB</th><th class="extra-col">NF / dB</th><th class="extra-col">IIP3 / dBm</th><th class="extra-col">IP1 / dBm</th><th class="row-actions-heading" aria-label="器件操作"></th></tr></thead><tbody>${stages.slice(state.page*size,(state.page+1)*size).map(s=>`<tr data-row="${esc(s.id)}" class="${s.id===state.selected?'selected':''} ${s.enabled?'':'off'}" aria-selected="${s.id===state.selected}"><td class="drag-cell"><button class="icon ghost row-drag" data-drag-stage="${esc(s.id)}" aria-label="拖动${esc(s.name)}" title="拖动排序" aria-keyshortcuts="ArrowUp ArrowDown">${icon('grip')}</button></td><td><input type="checkbox" data-cell="enabled" data-id="${esc(s.id)}" ${s.enabled?'checked':''} aria-label="启用${esc(s.name)}"></td><td class="name"><input value="${esc(s.name)}" data-cell="name" data-id="${esc(s.id)}" aria-label="器件名称 ${s.order}" title="${esc(s.name)}"></td><td class="type"><button data-select-stage="${esc(s.id)}" title="编辑器件类型">${esc(config.labels.TYPES[s.type])}</button></td>${[['gain_db',s.gain_db,'增益'],['nf',actualNF(s),'NF'],['iip3',inputPower(s,'ip3'),'IIP3'],['ip1',inputPower(s,'p1db'),'IP1']].map(([key,v,label],i)=>`<td class="metric-cell ${i?'extra-col':''}"><input type="number" step="any" value="${modelBinding(s,key)?(modelBinding(s,key).value==null?'':num(modelBinding(s,key).value)):v==null?'':num(v)}" ${modelBinding(s,key)?'disabled':''} placeholder="${(key==='iip3'&&s.ip3.mode==='ideal')||(key==='ip1'&&s.p1db.mode==='ideal')?'∞':'—'}" data-cell="${key}" data-id="${esc(s.id)}" aria-label="${label} ${s.order}" title="${key==='iip3'||key==='ip1'?'输入参考；修改后以输入值保存':label}"></td>`).join('')}<td class="row-actions">${stageAction('copy','copy',s)}${stageAction('trash','delete',s)}</td></tr>`).join('')}</tbody></table>`;
  $('#table-pagination').innerHTML = `<span class="muted">${stages.length} 项</span><span class="spacer"></span>${button('left','previous','',state.page===0?'disabled':'')}<span>${state.page+1} / ${pages}</span>${button('right','next','',state.page>=pages-1?'disabled':'')}`;
}

function stageAction(symbol, action, stage) {
  const label = `${action==='copy'?'复制':'删除'}${stage.name}`;
  return `<button class="icon ghost" data-action="${action}" data-action-stage="${esc(stage.id)}" aria-label="${esc(label)}" title="${esc(label)}" ${action==='copy'&&state.project.stages.length>=200?'disabled':''}>${icon(symbol)}</button>`;
}

function updateStructureButtons() {
  $$('[data-action="add"],[data-action="copy"]', $('#workspace')).forEach(el => {
    el.disabled = state.project.stages.length >= 200;
  });
}

function renderInspector() {
  const target=$('#inspector'); if(!target) return;
  const s=currentStage();
  if(!s) {target.innerHTML='<div class="panel-head"><h2>器件参数</h2></div><div class="empty">—</div>'; return;}
  const previousScroll=$('.inspector-fields',target)?.scrollTop || 0;
  const L=config.labels;
  let contents='';
  if(state.inspectorTab==='parameters') {
    contents = field('器件名称',`<input value="${esc(s.name)}" data-stage-field="name" aria-label="器件名称" maxlength="4096">`) +
      stageSelect('器件类型','type',L.TYPES,s.type) + stageInput('器件增益','gain_db',s.gain_db,'dB') +
      stageSelect('噪声模式','noise.mode',L.NOISE,s.noise.mode) + stageInput('噪声系数','noise.value_db',s.noise.mode==='passive_thermal'?compactNumber(actualNF(s)):s.noise.value_db,'dB',s.noise.mode!=='manual'?'disabled':'') +
      '<div class="field-rule"></div>' + stageSelect('IP3模式','ip3.mode',L.POWER,s.ip3.mode) +
      stageSelect('IP3参考','ip3.reference',L.IP3REF,s.ip3.reference,s.ip3.mode!=='finite'?'disabled':'') +
      stageInput('IP3数值','ip3.value_dbm',s.ip3.value_dbm,'dBm',s.ip3.mode!=='finite'?'disabled':'') +
      stageSelect('P1模式','p1db.mode',L.POWER,s.p1db.mode) +
      stageSelect('P1参考','p1db.reference',L.P1REF,s.p1db.reference,s.p1db.mode!=='finite'?'disabled':'') +
      stageInput('P1数值','p1db.value_dbm',s.p1db.value_dbm,'dBm',s.p1db.mode!=='finite'?'disabled':'') +
      '<div class="field-rule"></div>' + stageInput('物理温度','physical_temperature_k',s.physical_temperature_k,'K') +
      stageInput('压缩形状','compression_p',s.compression_p,'',`min="1" max="10" placeholder="继承 ${state.project.analysis.default_compression_p}"`) +
      stageInput('最大输入','absolute_max_input_dbm',s.absolute_max_input_dbm,'dBm');
  } else if(state.inspectorTab==='models') {
    contents = renderModelInspector(s);
  } else if(state.inspectorTab==='frequency') {
    contents = field('适用范围',`<button class="switch ${s.frequency_range_hz?'on':''}" role="switch" aria-checked="${!!s.frequency_range_hz}" aria-label="启用频率范围" data-action="toggle-range"></button>`);
    if(s.frequency_range_hz) contents += stageInput('频率下限','frequency_range_hz.0',s.frequency_range_hz[0]/1e6,'MHz','data-scale="1000000"') + stageInput('频率上限','frequency_range_hz.1',s.frequency_range_hz[1]/1e6,'MHz','data-scale="1000000"');
    if(s.type==='mixer') {
      const m=s.mixer || {lo_frequency_hz:2.3e9,relation:'difference',noise_convention:'unknown',noise_compatible:false,lo_power_dbm:null};
      contents += '<div class="field-rule"></div>' + stageInput('LO频率','mixer.lo_frequency_hz',m.lo_frequency_hz/1e6,'MHz','data-scale="1000000"') + stageInput('LO功率','mixer.lo_power_dbm',m.lo_power_dbm,'dBm') +
        stageSelect('变频关系','mixer.relation',{difference:'差频',sum:'和频'},m.relation) + stageSelect('NF口径','mixer.noise_convention',{unknown:'未知',SSB:'SSB',DSB:'DSB'},m.noise_convention) +
        field('口径确认',`<button class="switch ${m.noise_compatible?'on':''}" role="switch" aria-checked="${m.noise_compatible}" aria-label="噪声口径确认" data-action="toggle-compatible"></button>`);
    }
  } else {
    const source=s.source || {};
    for(const [key,label] of [['kind','来源类型'],['url','数据来源'],['version','数据版本'],['bias','偏置条件']]) contents += field(label,`<input value="${esc(source[key] || '')}" data-stage-field="source.${key}" aria-label="${label}" maxlength="4096">`);
    contents += stageInput('测试频率','source.test_frequency_hz',source.test_frequency_hz==null?null:source.test_frequency_hz/1e6,'MHz','data-scale="1000000"') + stageInput('测试温度','source.temperature_k',source.temperature_k,'K') +
      stageSelect('规格性质','source.specification',{'':'未声明',typ:'典型值',min:'最小值',max:'最大值',assumed:'人工假设'},source.specification || '') +
      field('条件差异',`<button class="switch ${source.conditions_mismatch?'on':''}" role="switch" aria-checked="${!!source.conditions_mismatch}" aria-label="测试条件不一致" data-action="toggle-mismatch"></button>`) +
      field('来源说明',`<textarea data-stage-field="source.note" aria-label="来源说明" maxlength="4096">${esc(source.note || '')}</textarea>`,'multiline') +
      field('器件备注',`<textarea data-stage-field="notes" aria-label="器件备注" maxlength="4096">${esc(s.notes || '')}</textarea>`,'multiline');
  }
  target.innerHTML=`<div class="panel-head"><h2 title="${esc(s.name)}">${esc(s.name || '器件参数')}</h2><span class="spacer"></span><button class="switch ${s.enabled?'on':''}" role="switch" aria-checked="${s.enabled}" aria-label="启用器件" title="${esc(s.name)} · ${s.enabled?'已启用':'已停用'}" data-action="toggle-stage"></button><button class="icon ghost inspector-close" data-action="close-inspector" aria-label="关闭器件参数">${icon('close')}</button></div><nav class="panel-tabs" aria-label="器件参数分类">${[['parameters','参数'],['frequency','频率'],['models','模型'],['source','来源']].map(([key,label])=>`<button data-inspector-tab="${key}" class="${state.inspectorTab===key?'active':''}">${label}</button>`).join('')}</nav><div class="inspector-fields hide-scroll ${state.inspectorTab==='parameters'?'parameters-layout':''}">${contents}</div>`;
  $('.inspector-fields',target).scrollTop=previousScroll;
}

function primitives(kind) {
  return (config.symbols[kind] || config.symbols.custom).map(p=>{
    if(p.kind==='rect') return `<rect class="symbol" x="${p.points[0][0]}" y="${p.points[0][1]}" width="${p.points[1][0]}" height="${p.points[1][1]}"/>`;
    if(p.kind==='ellipse') return `<ellipse class="symbol" cx="${p.points[0][0]+p.points[1][0]/2}" cy="${p.points[0][1]+p.points[1][1]/2}" rx="${p.points[1][0]/2}" ry="${p.points[1][1]/2}"/>`;
    if(p.kind==='polyline'||p.kind==='polygon') return `<${p.kind} class="symbol ${p.kind==='polyline'?'symbol-line':''}" points="${p.points.map(x=>x.join(',')).join(' ')}"/>`;
    return `<text x="${p.points[0][0]}" y="${p.points[0][1]}" font-size="16">${esc(p.text)}</text>`;
  }).join('');
}

function renderChain() {
  const target=$('#chain-viewport'); if(!target) return;
  const all=state.project.stages;
  const size=state.view==='diagram'?8:Math.min(6,effectivePageSize());
  const pageStart=state.page*effectivePageSize();
  const selectedIndex=all.findIndex(s=>s.id===state.selected);
  const previewOffset=selectedIndex>=pageStart&&selectedIndex<pageStart+effectivePageSize()?Math.floor((selectedIndex-pageStart)/size)*size:0;
  const start=state.view==='diagram'?state.diagramPage*8:pageStart+previewOffset;
  const stages=all.slice(start,state.view==='diagram'?start+size:Math.min(start+size,pageStart+effectivePageSize()));
  if(!stages.length) {target.innerHTML=`<div class="empty"><button data-action="add">${icon('plus')}添加器件</button></div>`;return;}
  const detailed=state.view==='diagram', hasMixer=stages.some(s=>s.type==='mixer');
  const scale=.64, pitch=detailed?260:235, left=90, y=hasMixer?155:88;
  const width=left*2+pitch*stages.length, height=y+(detailed?148:83);
  const points=stages.map((s,i)=>({s,x:left+i*pitch+(pitch-240*scale)/2,y:y-50*scale}));
  let drawing=`<defs><marker id="chain-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 10 5 0 10z" fill="#202830"/></marker></defs>`;
  let prev=24;
  points.forEach(({s,x,y:sy})=>{
    drawing+=`<path class="wire" d="M${prev} ${y}H${x}" marker-end="url(#chain-arrow)"/>`;
    const shortName=s.name.length>14?s.name.slice(0,13)+'…':s.name;
    drawing+=`<g data-stage="${esc(s.id)}" role="button" tabindex="0" aria-label="选择${esc(s.name)}" class="${s.id===state.selected?'chosen':''} ${s.enabled?'':'bypassed'}"><title>${esc(s.name)}</title><rect class="node-selection" x="${x-9}" y="${sy-12}" width="${240*scale+18}" height="${100*scale+24}" rx="6"/><g transform="translate(${x},${sy}) scale(${scale})">${primitives(s.type)}</g><text class="node-name" x="${x+120*scale-(s.type==='mixer'?25:0)}" y="${sy-22}" text-anchor="${s.type==='mixer'?'end':'middle'}">${esc(shortName)}</text><text class="node-value" x="${x+120*scale}" y="${y+57}" text-anchor="middle">${s.enabled?num(modelBinding(s,'gain_db')?modelBinding(s,'gain_db').value:s.gain_db)+' dB':'旁路'}</text>`;
    if(detailed) drawing+=`<text class="node-value" x="${x+120*scale}" y="${y+79}" text-anchor="middle">NF ${num(actualNF(s))} dB · IIP3 ${stagePowerText(s,'ip3')} dBm</text><text class="node-value" x="${x+120*scale}" y="${y+101}" text-anchor="middle">IP1 ${stagePowerText(s,'p1db')} dBm</text>`;
    drawing+='</g>';
    if(s.type==='mixer' && s.mixer) {
      const lx=x+120*scale, ly=34;
      drawing+=`<g transform="translate(${lx-60},${ly-25}) scale(.5)">${primitives('lo')}</g><path class="aux" d="M${lx} ${ly+15}V${sy}" marker-end="url(#chain-arrow)"/><text class="node-value" x="${lx}" y="12" text-anchor="middle">LO ${num(s.mixer.lo_frequency_hz/1e6)} MHz</text>`;
    }
    prev=x+240*scale;
  });
  drawing+=`<path class="wire" d="M${prev} ${y}H${width-24}" marker-end="url(#chain-arrow)"/><circle cx="24" cy="${y}" r="5" fill="white" stroke="#202830"/><circle cx="${width-24}" cy="${y}" r="5" fill="white" stroke="#202830"/><text class="node-name" x="24" y="${y-20}" text-anchor="middle">${start?'接前段':'输入'}</text><text class="node-name" x="${width-24}" y="${y-20}" text-anchor="middle">${start+stages.length<all.length?'接后段':'输出'}</text>`;
  target.innerHTML=`<svg class="chain-svg" viewBox="0 0 ${width} ${height}" role="group" aria-label="${esc(state.project.link_name)}链路图"><g id="diagram-transform" transform="translate(${state.pan.x},${state.pan.y}) translate(${width/2},${height/2}) scale(${state.zoom}) translate(${-width/2},${-height/2})">${drawing}</g></svg>`;
  $$('.zoom-level').forEach(e=>{e.textContent=Math.round(state.zoom*100)+'%';});
}

const summaryKeys=[['gain_db','总增益'],['nf_db','噪声系数'],['iip3_dbm','系统 IIP3'],['ip1_dbm','输入 P1'],['linear_output_dbm','线性输出'],['sfdr3_db','SFDR']];
function metricTitle(m) {return m ? `${config.labels.STATUS[m.status]} · ${m.reference}${m.reason?' · '+m.reason:''}${m.model?' · '+m.model:''}` : '请计算';}
function renderMetrics() {
  const target=$('#metrics'), dirty=stale(); target.classList.toggle('stale',dirty);
  target.innerHTML=summaryKeys.map(([key,label])=>{const m=state.result?.metrics[key];return `<div class="metric" tabindex="0" role="button" data-metric="${key}" aria-label="${label}详情" title="${esc(dirty?'输入已更改，待计算':metricTitle(m))}"><span class="metric-label">${label}</span><span class="metric-value">${dirty?'—':metricNumber(m)}<span class="metric-unit">${m?.value==null&&!dirty?'':esc(m?.unit || config.labels.METRICS[key][1])}</span></span></div>`;}).join('');
}

const shortLabel = key => (config.labels.METRICS[key]?.[0] || key).replace(/（[^）]*）/g,'');
const extraLabels = {frequency_input_hz:'输入频率',stage_gain_db:'本级增益',stage_nf_db:'本级NF',linear_input_dbm:'线性输入',noise_contribution_k:'噪声温度贡献',stage_iip3_dbm:'本级IIP3',stage_oip3_dbm:'本级OIP3',stage_ip1_dbm:'本级输入P1',compressed_input_dbm:'估算输入',stage_compression_db:'本级压缩',p1_margin_db:'P1裕量',spectrum_inverted:'频谱翻转'};
const stageGroups = {power:['linear_input_dbm','linear_output_dbm','compressed_input_dbm','compressed_output_dbm','stage_compression_db','compression_db'],noise:['stage_gain_db','gain_db','stage_nf_db','nf_db','noise_contribution_k','noise_output_dbm','snr_db'],linearity:['stage_iip3_dbm','iip3_dbm','oip3_dbm','stage_ip1_dbm','p1_margin_db','tone_output_dbm','im3_dbm'],frequency:['frequency_input_hz','frequency_output_hz','spectrum_inverted']};
state.stageGroup='power'; state.overviewChart='power';

function stat(key) {
  const m=state.result.metrics[key];
  return `<div class="analysis-stat" tabindex="0" role="button" data-metric="${key}" title="${esc(metricTitle(m))}"><span>${esc(shortLabel(key))}</span><strong class="num">${metricNumber(m)} <small>${m.value==null?'':esc(m.unit)}</small></strong></div>`;
}

function fitAnalysisPage() {
  const target=$('#analysis-content'); if(!target || state.analysisTab!=='stages') return;
  const capacity=Math.max(1,Math.floor((target.clientHeight-115)/35));
  if(capacity!==state.analysisCapacity) {state.analysisCapacity=capacity;renderAnalysis();}
}

function renderAnalysis() {
  const target=$('#analysis-content'); if(!target) return;
  if(labTabs.some(([key])=>key===state.analysisTab)){renderLabAnalysis(target);return;}
  if(stale()) {target.innerHTML=`<div class="empty"><button class="primary" data-action="calculate">${icon('play')}计算</button></div>`;return;}
  if(state.analysisTab==='overview') {
    target.innerHTML=`<div class="analysis-grid"><div class="chart-wrap"><div class="row chart-title"><select data-overview-chart aria-label="趋势指标">${options({power:'逐级功率',gain:'累计增益',noise:'累计噪声系数'},state.overviewChart)}</select></div><div id="plot" class="chart-wrap" style="flex:1"></div></div><div class="analysis-metrics">${['noise_input_dbm','noise_output_dbm','snr_db','sensitivity_dbm','compression_dr_db','op1_dbm'].map(stat).join('')}</div></div>`;
    const rows=state.result.stages;
    const series=state.overviewChart==='power'?[{label:'信号',key:'linear_output_dbm',color:'#087f8c'},{label:'噪声',key:'noise_output_dbm',color:'#95a8b5'}]:[{label:state.overviewChart==='gain'?'增益':'NF',key:state.overviewChart==='gain'?'gain_db':'nf_db',color:'#087f8c'}];
    requestAnimationFrame(()=>plot(series.map(s=>({...s,points:rows.map((row,i)=>({x:i+1,y:row.metrics[s.key]?.value,name:row.name}))})),{x:'级数',y:state.overviewChart==='power'?'dBm':'dB'}));
  } else if(state.analysisTab==='stages') {
    const all=state.result.stages, count=state.analysisCapacity, pages=Math.max(1,Math.ceil(all.length/count));
    state.analysisPage=Math.min(state.analysisPage,pages-1);
    const keys=stageGroups[state.stageGroup];
    target.innerHTML=`<div class="row" style="height:40px;flex-shrink:0"><select data-stage-group aria-label="逐级指标分类">${options({power:'功率与压缩',noise:'增益与噪声',linearity:'IP3与P1',frequency:'频率'},state.stageGroup)}</select></div><div class="analysis-table-wrap"><table class="analysis-table" aria-label="逐级结果"><thead><tr><th>器件</th>${keys.map(k=>`<th>${esc(extraLabels[k] || shortLabel(k))}</th>`).join('')}</tr></thead><tbody>${all.slice(state.analysisPage*count,(state.analysisPage+1)*count).map(row=>`<tr><td title="${esc(row.name)}">${esc(row.name)}</td>${keys.map(k=>{const m=row.metrics[k];return `<td title="${esc(metricTitle(m))}" data-stage-metric="${k}" data-result-stage="${esc(row.stage_id)}" tabindex="0">${metricNumber(m)} <span class="muted">${m?.value==null?'':esc(m?.unit || '')}</span></td>`;}).join('')}</tr>`).join('')}</tbody></table></div><div class="pagination">${button('left','analysis-previous','',state.analysisPage===0?'disabled':'')}<span>${state.analysisPage+1} / ${pages}</span>${button('right','analysis-next','',state.analysisPage>=pages-1?'disabled':'')}</div>`;
  } else if(state.analysisTab==='compression') {
    const p1=state.result.metrics.ip1_dbm, gain=state.result.metrics.gain_db;
    if(gain.value==null || (!['estimated','ideal'].includes(p1.status)&&!Object.values(state.project.lab?.models||{}).some(m=>m.nonlinear))) {target.innerHTML=`<div class="empty"><button data-action="find-p1">${icon('sliders')}P1参数</button></div>`;return;}
    const center=p1.value ?? state.project.analysis.input_power_dbm;
    const low=state.scanLow ?? Math.min(center-40,state.project.analysis.input_power_dbm-10), high=state.scanHigh ?? Math.max(center+10,state.project.analysis.input_power_dbm+10);
    target.innerHTML=`<div class="scan-controls"><label for="scan-low">输入范围</label><input id="scan-low" type="number" value="${num(low)}" aria-label="扫描下限"><span>—</span><input id="scan-high" type="number" value="${num(high)}" aria-label="扫描上限"><span class="muted">dBm</span><button data-action="scan">${icon('play')}扫描</button></div><div class="chart-wrap" id="plot" style="flex:1"></div>`;
    if(state.scan) drawScan(); else void runScan(low,high);
  } else {
    const t=state.project.analysis.two_tone;
    if(!t.enabled) {target.innerHTML=`<div class="empty"><button data-action="enable-tone">${icon('plus')}启用双音</button></div>`;return;}
    target.innerHTML=`<div class="scan-controls"><label for="tone-pin">每音功率</label><input id="tone-pin" type="number" step="any" value="${t.each_tone_power_dbm}" data-condition="two_tone.each_tone_power_dbm" aria-label="每音功率"><span class="muted">dBm</span><label for="tone-spacing">间隔</label><input id="tone-spacing" type="number" step="any" value="${t.spacing_hz/1e3}" data-condition="two_tone.spacing_hz" data-scale="1000" aria-label="双音间隔"><span class="muted">kHz</span></div><div class="analysis-grid"><div class="chart-wrap" id="plot"></div><div class="tone-values">${['tone_input_dbm','tone_total_dbm','tone_output_dbm','im3_dbm','im3_dbc','tone_span_db'].map(stat).join('')}</div></div>`;
    let frequencies=[-1.5,-.5,.5,1.5].map(offset=>state.project.analysis.source_frequency_hz+offset*t.spacing_hz);
    for(const s of state.project.stages) if(s.enabled && s.type==='mixer' && s.mixer) frequencies=frequencies.map(f=>s.mixer.relation==='sum'?f+s.mixer.lo_frequency_hz:Math.abs(f-s.mixer.lo_frequency_hz));
    const fundamental=state.result.metrics.tone_output_dbm.value, im3=state.result.metrics.im3_dbm.value;
    requestAnimationFrame(()=>plot([{label:'基波',color:'#087f8c',points:[1,2].map(i=>({x:frequencies[i]/1e6,y:fundamental}))},{label:'IM3',color:'#95a8b5',points:[0,3].map(i=>({x:frequencies[i]/1e6,y:im3}))}],{x:'MHz',y:'dBm',stems:true}));
  }
}

async function runScan(low,high) {
  const revision=state.revision;
  try {
    const response=await api('scan',{project:state.project,low,high});
    if(revision!==state.revision) return;
    labUI.requests.compression={kind:'power',parameters:{low,high,count:161}};state.scan=response.points;state.scanLow=low;state.scanHigh=high;
    if(state.view==='analysis'&&state.analysisTab==='compression') drawScan();
  } catch(error) {toast(error.message,true);}
}

function drawScan() {
  if(!state.scan) return;
  requestAnimationFrame(()=>plot([{label:'线性输出',color:'#95a8b5',points:state.scan.map(p=>({x:p.x,y:p.linear}))},{label:'压缩输出',color:'#087f8c',points:state.scan.map(p=>({x:p.x,y:p.compressed}))}],{x:'输入 / dBm',y:'输出 / dBm'}));
}

function plot(series,axes) {
  const target=$('#plot');if(!target) return;
  const valid=series.flatMap(s=>s.points).filter(p=>typeof p.y==='number' && Number.isFinite(p.x) && Number.isFinite(p.y));
  if(!valid.length) {target.innerHTML='<div class="empty">—</div>';return;}
  const width=Math.max(260,target.clientWidth),height=Math.max(125,target.clientHeight-27);
  const box={x:64,y:22,w:width-82,h:height-66};
  let xmin=Math.min(...valid.map(p=>p.x)),xmax=Math.max(...valid.map(p=>p.x)),ymin=Math.min(...valid.map(p=>p.y)),ymax=Math.max(...valid.map(p=>p.y));
  if(xmin===xmax){xmin-=.5;xmax+=.5;}
  if(ymin===ymax){ymin-=5;ymax+=5;}
  const ypadding=(ymax-ymin)*.12;ymin-=ypadding;ymax+=ypadding;
  if(axes.stems||axes.scatter){const xp=(xmax-xmin)*.12;xmin-=xp;xmax+=xp;if(axes.stems)ymin-=15;}
  const X=x=>box.x+(x-xmin)/(xmax-xmin)*box.w,Y=y=>box.y+box.h-(y-ymin)/(ymax-ymin)*box.h;
  const tick=(v,step)=>Math.abs(v)>=1e5||step<1e-5?v.toExponential(3):Number(v.toFixed(Math.max(1,Math.min(6,Math.ceil(-Math.log10(step))+1))));
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${axes.x}与${axes.y}曲线"><defs><clipPath id="plot-clip"><rect x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}"/></clipPath></defs><g font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="11" fill="#6c7e8b">`;
  const ticks=width<420?3:5;
  for(let i=0;i<=ticks;i++) {
    const x=xmin+(xmax-xmin)*i/ticks,y=ymin+(ymax-ymin)*i/ticks;
    svg+=`<path d="M${box.x} ${Y(y)}H${width-18}" stroke="#e9eef2" fill="none"/><text x="${box.x-9}" y="${Y(y)+4}" text-anchor="end">${tick(y,(ymax-ymin)/ticks)}</text><text x="${X(x)}" y="${height-23}" text-anchor="middle">${tick(x,(xmax-xmin)/ticks)}</text>`;
  }
  svg+=`<text x="8" y="13">${esc(axes.y)}</text><text x="${width/2}" y="${height-4}" text-anchor="middle">${esc(axes.x)}</text><g clip-path="url(#plot-clip)">`;
  series.forEach(s=>{
    let path='',previous=false;
    s.points.forEach(p=>{
      if(p.y==null || !Number.isFinite(p.y)){previous=false;return;}
      const x=X(p.x),y=Y(p.y);
      if(axes.stems) svg+=`<path d="M${x} ${Y(ymin)}V${y}" stroke="${s.color}" stroke-width="3"/><circle cx="${x}" cy="${y}" r="4" fill="${s.color}"><title>${esc(s.label)} ${p.x.toFixed(4)} MHz · ${num(p.y)} dBm</title></circle>`;
      else {path+=`${previous?'L':'M'}${x.toFixed(2)} ${y.toFixed(2)} `;previous=true;}
    });
    if(!axes.stems) {
      if(!axes.scatter)svg+=`<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2.2"/>`;
      if(s.points.length<=20) s.points.forEach(p=>{if(p.y!=null) svg+=`<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="3.5" fill="${s.color}"><title>${esc(p.name || p.x)} · ${esc(s.label)} ${num(p.y)} ${esc(axes.y)}</title></circle>`;});
    }
  });
  target.innerHTML=svg+`</g></g></svg><div class="chart-legend">${series.map(s=>`<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join('')}</div>`;
}

function openModal(title,body,footer='',kind='generic') {
  const dialog=$('#modal'); state.modal=kind;
  dialog.innerHTML=`<div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button class="icon ghost" data-action="close" aria-label="关闭">${icon('close')}</button></div><div class="dialog-body hide-scroll"><div class="dialog-error" id="dialog-error" role="alert"></div>${body}</div>${footer?`<div class="dialog-foot">${footer}</div>`:''}`;
  if(!dialog.open) dialog.showModal();
}

function modalError(error) {
  const target=$('#dialog-error'); if(target) target.textContent=error.message || String(error); else toast(error.message || error,true);
}

async function projectPicker() {
  let saved='';
  if(config.account){const response=await fetch('/api/projects');if(!response.ok){location.assign('/');return;}const result=await response.json();saved=`<div class="section-label">我的项目</div><div class="project-list">${result.projects.map(p=>`<button data-saved-project="${esc(p.id)}">${icon('folder')}${esc(p.name)} · ${esc(p.link)}</button>`).join('')}</div>`;}
  const body=`<div class="row" style="margin-bottom:16px">${button('folder','open','打开')}${button('edit','project-edit','项目属性')}<span class="spacer"></span>${button('new','new','新建')}</div>${saved}<div class="section-label">示例</div><div class="project-list">${Object.keys(config.examples).map(name=>`<button data-example="${esc(name)}">${icon('wave')}${esc(name)}</button>`).join('')}</div>`;
  openModal('项目',body,'','projects');
}

function projectModal(isNew) {
  const project=isNew?config.new_project:state.project;
  openModal(isNew?'新建项目':'项目属性',field('项目名称',`<input id="project-title-input" value="${esc(project.project_name)}" aria-label="项目名称" maxlength="4096">`)+field('链路名称',`<input id="link-title-input" value="${esc(project.link_name)}" aria-label="链路名称" maxlength="4096">`)+field('链路类型',`<select id="link-type-input" aria-label="链路类型">${options(config.labels.LINK_TYPES,project.link_type)}</select>`)+field('备注',`<textarea id="project-notes" aria-label="项目备注" maxlength="4096">${esc(project.notes)}</textarea>`,'multiline'),`<button data-action="close">取消</button><button class="primary" data-action="${isNew?'create-project':'update-project'}">${isNew?'创建':'保存'}</button>`,isNew?'new':'project');
}

function conditionsModal(tab='basic') {
  const a=state.project.analysis;
  const conditionInput=(label,path,value,unit,scale=1)=>field(label,inputBox(value,unit,`data-dialog-condition="${path}" data-scale="${scale}" aria-label="${label}"`));
  let body=`<nav class="modal-tabs">${[['basic','基础'],['tone','双音'],['compression','压缩']].map(([k,label])=>`<button data-condition-tab="${k}" class="${k===tab?'active':''}">${label}</button>`).join('')}</nav>`;
  if(tab==='basic') body+=conditionInput('输入功率','input_power_dbm',a.input_power_dbm,'dBm')+conditionInput('工作频率','source_frequency_hz',a.source_frequency_hz/1e6,'MHz',1e6)+conditionInput('信号带宽','signal_bandwidth_hz',a.signal_bandwidth_hz/1e6,'MHz',1e6)+conditionInput('噪声带宽','noise_bandwidth_hz',a.noise_bandwidth_hz/1e6,'MHz',1e6)+conditionInput('源温度','source_noise_temperature_k',a.source_noise_temperature_k,'K')+conditionInput('所需SNR','required_snr_db',a.required_snr_db,'dB')+conditionInput('实现损失','implementation_loss_db',a.implementation_loss_db,'dB');
  else if(tab==='tone') body+=field('启用双音',`<button class="switch ${a.two_tone.enabled?'on':''}" role="switch" aria-checked="${a.two_tone.enabled}" aria-label="启用双音" data-action="modal-tone"></button>`)+conditionInput('每音功率','two_tone.each_tone_power_dbm',a.two_tone.each_tone_power_dbm,'dBm')+conditionInput('两音间隔','two_tone.spacing_hz',a.two_tone.spacing_hz/1e3,'kHz',1e3)+field('总平均功率',`<span class="num">${num(a.two_tone.each_tone_power_dbm+10*Math.log10(2))} dBm</span>`);
  else body+=conditionInput('压缩回退','compression_backoff_db',a.compression_backoff_db,'dB')+conditionInput('默认形状p','default_compression_p',a.default_compression_p,'');
  openModal('分析条件',body,`<button class="primary" data-action="close">完成</button>`,'conditions');
}

function exportModal() {
  if(stale()) {toast('请先计算',true);return;}
  openModal('导出',`<div class="dialog-grid">${[['zip','export','工程包'],['xlsx','table','工作簿'],['svg','wave','矢量图'],['drawio','edit','可编辑框图'],['json','save','项目文件'],['csv','table','器件表']].map(([key,ic,label])=>`<button data-export-kind="${key}">${icon(ic)}${label}</button>`).join('')}</div><div class="field-row" style="margin-top:18px"><label for="strict-export">严格检查</label><input id="strict-export" type="checkbox" ${state.strict?'checked':''} aria-label="严格检查"></div>`,`<button data-action="save-bundle">${icon('save')}保存工程包</button>`,'export');
}

function settingsModal() {
  if(config.account){openModal('账户设置',field('账户邮箱',`<span>${esc(config.account.email)}</span>`)+field('当前密码','<input type="password" id="account-old" autocomplete="current-password" aria-label="当前密码">')+field('新密码','<input type="password" id="account-new" autocomplete="new-password" aria-label="新密码" minlength="12" maxlength="128">'),'<button data-action="logout">退出登录</button><button class="primary" data-action="password-change">修改密码</button>','account');return;}

  openModal('设置',field('数据目录',`<input id="data-directory" value="${esc(state.directory)}" aria-label="数据目录">`)+`<div class="row" style="margin-top:12px"><button data-action="diagnostics">${icon('sliders')}环境检查</button></div><div id="diagnostics-result" class="diagnostics"></div>`,`<button class="primary" data-action="save-settings">保存</button>`,'settings');
}

function showMetric(key, stageId=null) {
  if(stale()) {toast('输入已更改，请先计算');return;}
  const m=stageId?state.result.stages.find(s=>s.stage_id===stageId)?.metrics[key]:state.result.metrics[key];
  if(!m) return;
  const reference={chain_input:'链路输入',chain_output:'链路输出',stage_input:'本级输入',stage_output:'本级输出'}[m.reference] || m.reference;
  openModal(extraLabels[key] || shortLabel(key),`<div class="result-detail">${field('数值',`<strong>${metricNumber(m,5)} ${m.value==null?'':esc(m.unit)}</strong>`)}${field('状态',`<span>${esc(config.labels.STATUS[m.status])}</span>`)}${field('参考点',`<span>${esc(reference)}</span>`)}${m.reason?`<pre>${esc(m.reason)}</pre>`:''}${m.assumptions.length?`<pre>${esc(m.assumptions.join('\n'))}</pre>`:''}</div>`,'','metric');
}

function allMetrics(page=0) {
  if(stale()) {toast('请先计算');return;}
  const rows=Object.entries(state.result.metrics),count=8,pages=Math.ceil(rows.length/count);state.metricsPage=Math.max(0,Math.min(page,pages-1));
  openModal('全部指标',`<table class="analysis-table"><tbody>${rows.slice(state.metricsPage*count,(state.metricsPage+1)*count).map(([k,m])=>`<tr data-metric="${k}" tabindex="0"><td>${esc(shortLabel(k))}</td><td title="${esc(metricTitle(m))}">${metricNumber(m,3)} ${m.value==null?'':esc(m.unit)}</td></tr>`).join('')}</tbody></table>`,`<button data-action="metric-prev" ${state.metricsPage===0?'disabled':''}>${icon('left')}</button><span>${state.metricsPage+1} / ${pages}</span><button data-action="metric-next" ${state.metricsPage===pages-1?'disabled':''}>${icon('right')}</button>`,'metrics');
}

function issuesModal() {
  if(stale()) {toast('请先计算');return;}
  openModal('检查结果',`<div class="issue-list">${state.result.issues.map(i=>{const stage=state.project.stages.find(s=>s.id===i.stage_id);return `<div class="issue-item"><strong>${esc(stage?.name || '分析条件')} · ${esc(i.code)}</strong><p>${esc(i.message)}</p>${i.stage_id?`<button data-locate="${esc(i.stage_id)}">${icon('sliders')}定位参数</button>`:''}</div>`;}).join('') || '<div class="empty">'+icon('check')+'</div>'}</div>`,'','issues');
}

function closeModal() {$('#modal').close();state.modal=null;}

function chooseStage(id,open=false) {
  if(!state.project.stages.some(s=>s.id===id)) return;
  state.selected=id;
  if(open) {
    const index=state.project.stages.findIndex(s=>s.id===id);
    state.page=Math.floor(index/effectivePageSize());state.inspectorOpen=true;
    if(state.view!=='chain') {state.view='chain';renderWorkspace();updateChrome();} else renderStageTable();
  }
  $$('[data-row]').forEach(row=>{row.classList.toggle('selected',row.dataset.row===id);row.setAttribute('aria-selected',row.dataset.row===id);});
  renderInspector();renderChain();updateStructureButtons();persist();
  if(open) {$('#inspector')?.classList.add('open');$('.inspector-overlay')?.classList.add('open');}
}

async function loadProject(project,{response=null,history=true}={}) {
  resetLabProject(project);
  if(project.lab?.circuit&&!project.stages.length){state.view='analysis';state.analysisTab='circuit';circuitUI.mode='edit';circuitUI.selected=0;}
  else if(state.analysisTab==='circuit'&&!project.lab?.circuit){state.view='chain';state.analysisTab='overview';}
  if(history && state.project) checkpoint();
  state.project=copy(project);state.result=null;state.computed=null;state.selected=project.stages[0]?.id || null;
  state.page=0;state.analysisPage=0;state.diagramPage=0;state.zoom=1;state.pan={x:0,y:0};state.scan=null;state.scanLow=null;state.scanHigh=null;state.revision++;
  if(response) {state.project=response.project;state.result=response.result;state.computed=fingerprint();}
  state.inspectorOpen=false;closeModal();renderConditions();renderWorkspace();updateChrome();renderMetrics();persist();
  if(!response) await calculate();
}

async function calculate() {
  if(state.busy) return;
  const revision=state.revision;
  state.busy=true;updateChrome();$$('.input-invalid').forEach(el=>el.classList.remove('input-invalid'));
  try {
    const response=await api('calculate',{project:state.project});
    if(revision!==state.revision) return;
    state.project=response.project;state.result=response.result;state.computed=fingerprint();state.scan=null;
    if(state.modal==='validation') closeModal();
    refresh({inspector:true,conditions:true});persist();
  } catch(error) {
    if(revision!==state.revision) return;
    const pattern=/analysis\.([\w.]+)|stages\.(\d+)\.([\w.]+)/g;
    for(const match of error.message.matchAll(pattern)) {
      if(match[1]) $$('[data-condition]').filter(el=>el.dataset.condition===match[1]).forEach(el=>el.classList.add('input-invalid'));
      else if(state.project.stages[Number(match[2])]) {chooseStage(state.project.stages[Number(match[2])].id,true);$$('[data-stage-field]').filter(el=>el.dataset.stageField===match[3]).forEach(el=>el.classList.add('input-invalid'));}
    }
    openModal('检查输入',`<div class="dialog-error">${esc(error.message)}</div>`,`<button class="primary" data-action="close">返回编辑</button>`,'validation');
  } finally {state.busy=false;updateChrome();}
}

function structure(action, stageId=state.selected) {
  const stages=state.project.stages,index=stages.findIndex(s=>s.id===stageId);
  if(['add','copy'].includes(action) && stages.length>=200){toast('最多200个器件',true);return;}
  if(action!=='add' && index<0) return;
  checkpoint();
  if(action==='add') {const stage=copy(config.new_stage);stage.id=crypto.randomUUID();stage.name='新器件';stages.splice(index+1,0,stage);state.selected=stage.id;}
  else if(action==='copy') {const stage=copy(stages[index]);stage.id=crypto.randomUUID();stage.name+='（副本）';if(state.project.lab?.models?.[stages[index].id])state.project.lab.models[stage.id]=copy(state.project.lab.models[stages[index].id]);stages.splice(index+1,0,stage);state.selected=stage.id;}
  else if(action==='delete') {if(state.project.lab?.models)delete state.project.lab.models[stages[index].id];stages.splice(index,1);state.selected=stages[Math.min(index,stages.length-1)]?.id || null;}
  stages.forEach((s,i)=>{s.order=i+1;});
  const selectedIndex=stages.findIndex(s=>s.id===state.selected);state.page=Math.max(0,Math.floor(selectedIndex/effectivePageSize()));
  changed({inspector:true});
}

function moveStageOnPage(sourceId, targetId, after) {
  const stages=state.project.stages, start=state.page*effectivePageSize();
  const visible=stages.slice(start,start+effectivePageSize());
  const from=visible.findIndex(s=>s.id===sourceId), target=visible.findIndex(s=>s.id===targetId);
  if(from<0 || target<0 || from===target) return;
  const destination=target+(after?1:0)-(from<target?1:0);
  if(destination===from) return;
  checkpoint();
  const [stage]=stages.splice(start+from,1);
  stages.splice(start+destination,0,stage);
  stages.forEach((s,i)=>{s.order=i+1;});
  state.selected=sourceId;
  changed({inspector:true});
}

let stageDrag=null, suppressRowClickUntil=0;
function finishStageDrag(commit=false) {
  const drag=stageDrag; stageDrag=null;
  if(!drag) return;
  if(drag.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
  $$('.stage-table tr').forEach(row=>row.classList.remove('dragging','drop-before','drop-after'));
  if(drag.active) suppressRowClickUntil=performance.now()+250;
  if(commit && drag.active && drag.targetId) moveStageOnPage(drag.sourceId,drag.targetId,drag.after);
}

document.addEventListener('pointerdown',event=>{
  const row=event.target.closest('.stage-table tr[data-row]');
  if(!row || event.button!==0 || (event.target.closest('input,select,button')&&!event.target.closest('[data-drag-stage]'))) return;
  const handle=event.target.closest('[data-drag-stage]') || row;
  stageDrag={sourceId:row.dataset.row,handle,pointerId:event.pointerId,x:event.clientX,y:event.clientY,active:false,targetId:null,after:false};
  handle.setPointerCapture(event.pointerId);
});
document.addEventListener('pointermove',event=>{
  const drag=stageDrag;
  if(!drag || event.pointerId!==drag.pointerId) return;
  if(!drag.active && Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<4) return;
  drag.active=true;
  const rows=$$('.stage-table tbody tr'), bounds=$('#stage-table').getBoundingClientRect();
  rows.forEach(row=>{row.classList.toggle('dragging',row.dataset.row===drag.sourceId);row.classList.remove('drop-before','drop-after');});
  drag.targetId=null;
  if(event.clientX<bounds.left || event.clientX>bounds.right || event.clientY<bounds.top || event.clientY>bounds.bottom) return;
  const row=rows.find(r=>event.clientY<r.getBoundingClientRect().bottom) || rows.at(-1);
  if(!row || row.dataset.row===drag.sourceId) return;
  const rect=row.getBoundingClientRect();
  drag.targetId=row.dataset.row; drag.after=event.clientY>rect.top+rect.height/2;
  row.classList.add(drag.after?'drop-after':'drop-before');
});
document.addEventListener('pointerup',()=>finishStageDrag(true));
document.addEventListener('pointercancel',()=>finishStageDrag());
window.addEventListener('blur',()=>finishStageDrag());
document.addEventListener('keydown',event=>{
  if(event.key==='Escape') finishStageDrag();
  const handle=event.target.closest('[data-drag-stage]');
  if(!handle || !['ArrowUp','ArrowDown'].includes(event.key)) return;
  event.preventDefault();
  const ids=$$('.stage-table tbody tr').map(row=>row.dataset.row), from=ids.indexOf(handle.dataset.dragStage);
  const down=event.key==='ArrowDown', target=ids[from+(down?1:-1)];
  if(target) {moveStageOnPage(handle.dataset.dragStage,target,down);$$('[data-drag-stage]').find(el=>el.dataset.dragStage===state.selected)?.focus();}
});

function historyAction(action) {
  const from=action==='undo'?state.history:state.future,to=action==='undo'?state.future:state.history;
  if(!from.length) return;
  to.push(copy(state.project));state.project=from.pop();
  state.selected=state.project.stages.some(s=>s.id===state.selected)?state.selected:state.project.stages[0]?.id || null;
  changed({inspector:true,conditions:true});
}

async function download(kind) {
  if(stale()) {toast('请先计算',true);return;}
  const strict=$('#strict-export')?.checked || false;state.strict=strict;
  const buttons=$$('[data-export-kind]');buttons.forEach(b=>{b.disabled=true;});
  try {
    const response=await api('export',{project:state.project,project_hash:state.result.project_hash,kind,strict},true);
    const filename=decodeURIComponent((response.headers.get('Content-Disposition') || '').split("UTF-8''")[1] || 'RF-Link.'+kind);
    const url=URL.createObjectURL(await response.blob());
    const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
    toast('已导出');
  } catch(error) {modalError(error);} finally {buttons.forEach(b=>{b.disabled=false;});}
}

async function action(name, target=null) {
  if(name.startsWith('lab-')){await labAction(name.slice(4),target);return;}
  if(['add','copy','delete'].includes(name)){structure(name,target?.dataset.actionStage || state.selected);return;}
  if(['undo','redo'].includes(name)){historyAction(name);return;}
  if(name==='calculate'){if(state.view==='analysis'&&state.analysisTab==='circuit'){if(circuitUI.mode==='edit'){circuitUI.mode='dc';renderAnalysis();}await labAction('run');}else await calculate();return;}
  if(name==='close'){closeModal();return;}
  if(name==='open'){$('#file-input').click();return;}
  if(name==='project-picker'){await projectPicker();return;}
  if(name==='new'){projectModal(true);return;}
  if(name==='project-edit'){projectModal(false);return;}
  if(name==='conditions'){conditionsModal();return;}
  if(name==='export'){if(state.view==='analysis'&&state.analysisTab==='circuit')await circuitAction('export-dialog');else exportModal();return;}
  if(name==='settings'){settingsModal();return;}
  if(name==='logout'||name==='password-change'){
    const response=await fetch('/auth/'+(name==='logout'?'logout':'password'),{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':config.account.csrf},body:JSON.stringify(name==='logout'?{}:{old:$('#account-old').value,password:$('#account-new').value})});
    if(!response.ok){const result=await response.json();modalError(result.error);return;}
    for(const key of Object.keys(sessionStorage)){if(key.startsWith('rf-link-workbench'))sessionStorage.removeItem(key);}
    location.assign('/');return;
  }
  if(name==='issues'){issuesModal();return;}
  if(name==='all-metrics'){allMetrics();return;}
  if(name==='metric-prev'){allMetrics(state.metricsPage-1);return;}
  if(name==='metric-next'){allMetrics(state.metricsPage+1);return;}
  if(name==='previous'||name==='next'){state.page+=name==='previous'?-1:1;state.zoom=1;state.pan={x:0,y:0};renderStageTable();renderChain();return;}
  if(name==='analysis-previous'||name==='analysis-next'){state.analysisPage+=name==='analysis-previous'?-1:1;renderAnalysis();return;}
  if(name==='fit'||name==='zoom-in'||name==='zoom-out') {if(name==='fit'){state.zoom=1;state.pan={x:0,y:0};}else state.zoom=Math.max(.4,Math.min(4,state.zoom*(name==='zoom-in'?1.2:1/1.2)));renderChain();return;}
  if(name==='inspector'){state.inspectorOpen=true;$('#inspector')?.classList.add('open');$('.inspector-overlay')?.classList.add('open');return;}
  if(name==='close-inspector'){state.inspectorOpen=false;$('#inspector')?.classList.remove('open');$('.inspector-overlay')?.classList.remove('open');return;}
  if(name==='find-p1') {const missing=state.project.stages.find(s=>s.enabled&&(s.p1db.mode==='unknown'||s.p1db.mode==='finite'&&s.p1db.value_dbm==null));chooseStage(missing?.id || state.project.stages[0]?.id,true);return;}
  if(name==='scan'){await runScan(Number($('#scan-low').value),Number($('#scan-high').value));return;}
  if(name==='enable-tone'||name==='modal-tone') {checkpoint();state.project.analysis.two_tone.enabled=name==='enable-tone'?true:!state.project.analysis.two_tone.enabled;changed({conditions:true});if(name==='modal-tone')conditionsModal('tone');return;}
  const s=currentStage();
  if(name==='toggle-stage'&&s){checkpoint();s.enabled=!s.enabled;changed({inspector:true});return;}
  if(name==='toggle-range'&&s){checkpoint();s.frequency_range_hz=s.frequency_range_hz?null:[1e6,3e9];changed({inspector:true});return;}
  if(name==='toggle-compatible'&&s){checkpoint();s.mixer.noise_compatible=!s.mixer.noise_compatible;changed({inspector:true});return;}
  if(name==='toggle-mismatch'&&s){checkpoint();s.source.conditions_mismatch=!s.source.conditions_mismatch;changed({inspector:true});return;}
  if(name==='create-project'||name==='update-project') {
    const title=$('#project-title-input').value.trim(),link=$('#link-title-input').value.trim(),type=$('#link-type-input').value,notes=$('#project-notes').value;
    if(!title||!link){modalError('请填写项目名称和链路名称');return;}
    if(name==='create-project'){const p=copy(config.new_project);p.project_id=crypto.randomUUID();Object.assign(p,{project_name:title,link_name:link,link_type:type,notes});await loadProject(p);}
    else {checkpoint();Object.assign(state.project,{project_name:title,link_name:link,link_type:type,notes});closeModal();changed();}
    return;
  }
  if(name==='save-settings'){state.directory=$('#data-directory').value.trim();persist();closeModal();toast('设置已保存');return;}
  if(name==='diagnostics') {
    try{const result=await api('diagnostics',{directory:$('#data-directory').value});$('#diagnostics-result').textContent=Object.entries(result).map(([k,v])=>`${k}：${v}`).join('\n');}catch(error){modalError(error);}return;
  }
  if(name==='save-bundle'&&config.account){await download('zip');return;}
  if(name==='save'||name==='save-bundle') {
    if(name==='save-bundle'&&stale()){toast('请先计算',true);return;}
    try{const response=await api(name==='save'?'save':'save-bundle',{project:state.project,directory:state.directory,project_hash:state.result?.project_hash,strict:$('#strict-export')?.checked || false});toast(response.manifest?.status==='partial_failure'?'已保存，部分格式未生成':'已保存');}
    catch(error){if($('#modal').open)modalError(error);else toast(error.message,true);}return;
  }
}

document.addEventListener('click',event=>{
  if(event.target.closest('.stage-table') && performance.now()<suppressRowClickUntil) return;
  const target=event.target.closest('button,[data-metric],[data-row],[data-stage],[data-stage-metric]');if(!target||!state.project) return;
  if(target.dataset.action){void action(target.dataset.action,target).catch(error=>{if($('#modal').open)modalError(error);else toast(error.message,true);});return;}
  if(target.dataset.view){state.view=target.dataset.view;state.zoom=1;state.pan={x:0,y:0};renderWorkspace();updateChrome();return;}
  if(target.dataset.inspectorTab){state.inspectorTab=target.dataset.inspectorTab;renderInspector();$('.inspector-fields').scrollTop=0;return;}
  if(target.dataset.analysis){state.analysisTab=target.dataset.analysis;state.analysisPage=0;renderWorkspace();return;}
  if(target.dataset.conditionTab){conditionsModal(target.dataset.conditionTab);return;}
  if(target.dataset.savedProject){void (async()=>{try{const response=await fetch('/api/projects/'+encodeURIComponent(target.dataset.savedProject));const result=await response.json();if(!response.ok)throw new Error(result.error);await loadProject(result.project);}catch(error){modalError(error);}})();return;}
  if(target.dataset.example){void loadProject(config.examples[target.dataset.example]);return;}
  if(target.dataset.selectStage){chooseStage(target.dataset.selectStage,true);return;}
  if(target.dataset.dragStage){chooseStage(target.dataset.dragStage);return;}
  if(target.dataset.stage){chooseStage(target.dataset.stage);return;}
  if(target.dataset.row){chooseStage(target.dataset.row);return;}
  if(target.dataset.locate){closeModal();chooseStage(target.dataset.locate,true);return;}
  if(target.dataset.exportKind){void download(target.dataset.exportKind);return;}
  if(target.dataset.metric){showMetric(target.dataset.metric);return;}
  if(target.dataset.stageMetric){showMetric(target.dataset.stageMetric,target.dataset.resultStage);return;}
  if(target.dataset.tone){const enabled=target.dataset.tone==='true';if(state.project.analysis.two_tone.enabled!==enabled){checkpoint();state.project.analysis.two_tone.enabled=enabled;changed({conditions:true});}return;}
});

document.addEventListener('change',event=>{
  const el=event.target;if(!state.project) return;
  if(el.dataset.unit){state[el.dataset.unit]=el.value;renderConditions();persist();return;}
  if(el.hasAttribute('data-diagram-page')){state.diagramPage=Number(el.value);state.zoom=1;state.pan={x:0,y:0};renderChain();return;}
  if(el.hasAttribute('data-overview-chart')){state.overviewChart=el.value;renderAnalysis();return;}
  if(el.hasAttribute('data-stage-group')){state.stageGroup=el.value;renderAnalysis();return;}
  if(el.dataset.condition||el.dataset.dialogCondition) {
    const key=el.dataset.condition || el.dataset.dialogCondition;
    let scale=Number(el.dataset.scale || 1);
    if(el.dataset.condition==='source_frequency_hz')scale=unitScales[state.frequencyUnit];
    if(el.dataset.condition==='noise_bandwidth_hz')scale=unitScales[state.bandwidthUnit];
    const value=el.value.trim()===''?null:Number(el.value)*scale;
    checkpoint();setPath(state.project.analysis,key,value);changed({conditions:!!el.dataset.dialogCondition});return;
  }
  if(el.dataset.cell) {
    const s=state.project.stages.find(s=>s.id===el.dataset.id);if(!s) return;
    state.selected=s.id;checkpoint();
    const key=el.dataset.cell,value=el.type==='checkbox'?el.checked:el.type==='number'?(el.value===''?null:Number(el.value)):el.value;
    if(key==='nf'){s.noise.mode='manual';s.noise.value_db=value;}
    else if(key==='iip3'||key==='ip1'){const spec=s[key==='iip3'?'ip3':'p1db'];spec.reference='input';spec.mode='finite';spec.value_dbm=value;}
    else s[key]=value;
    changed({inspector:true,table:false});
    for(const [derived,number] of [['nf',actualNF(s)],['iip3',inputPower(s,'ip3')],['ip1',inputPower(s,'p1db')]]) {
      const cell=$$('[data-cell]').find(input=>input.dataset.id===s.id&&input.dataset.cell===derived);
      if(cell&&cell!==el)cell.value=number==null?'':num(number);
    }
    return;
  }
  if(el.dataset.stageField) {
    const s=currentStage();if(!s)return;
    const path=el.dataset.stageField;let value=el.type==='number'?(el.value===''?null:Number(el.value)*Number(el.dataset.scale||1)):el.value;
    checkpoint();setPath(s,path,value);
    if(path.startsWith('source.')&&(value==null||value===''))delete s.source[path.split('.')[1]];
    if(path==='type'&&value==='mixer'&&!s.mixer)s.mixer={lo_frequency_hz:2.3e9,relation:'difference',noise_convention:'unknown',noise_compatible:false,lo_power_dbm:null};
    if(path==='type'&&['input','output','antenna','adc','dac','digital'].includes(value)){s.gain_db=0;s.noise={mode:'ideal',value_db:null};s.ip3={mode:'ideal',reference:'input',value_dbm:null};s.p1db={mode:'ideal',reference:'input',value_dbm:null};}
    const rebuild=path.endsWith('.mode')||path==='type';
    changed({inspector:rebuild});
  }
});

$('#file-input').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;
  if(file.size>32*1024*1024){toast('文件超出大小限制',true);event.target.value='';return;}
  try{const text=await file.text();const response=await api('import',{text,format:file.name.toLowerCase().endsWith('.csv')?'csv':'json',project:state.project});await loadProject(response.project,{response});toast('已打开');}
  catch(error){toast(error.message,true);}finally{event.target.value='';}
});

document.addEventListener('keydown',event=>{
  if(!state.project)return;
  if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();document.activeElement?.blur();void action('calculate');}
  else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();document.activeElement?.blur();void action('save');}
  else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='o'){event.preventDefault();$('#file-input').click();}
  else if((event.ctrlKey||event.metaKey)&&['z','y'].includes(event.key.toLowerCase())&&!['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)){event.preventDefault();historyAction(event.key.toLowerCase()==='y'||event.shiftKey?'redo':'undo');}
  else if(event.key==='Enter'&&event.target.matches('[data-stage],[data-metric],[data-stage-metric]')){event.preventDefault();event.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}
  else if(event.key==='Enter'&&event.target.matches('input:not([type=file])'))event.target.blur();
  else if(event.key==='Escape'&&!$('#modal').open&&state.inspectorOpen)void action('close-inspector');
});

document.addEventListener('wheel',event=>{
  if(event.target.matches('input[type=number]'))event.target.blur();
  const target=event.target.closest('#chain-viewport');
  if(target&&state.project){event.preventDefault();state.zoom=Math.max(.4,Math.min(4,state.zoom*(event.deltaY<0?1.12:1/1.12)));renderChain();}
},{passive:false});

document.addEventListener('pointerdown',event=>{
  const target=event.target.closest('#chain-viewport');if(!target||event.target.closest('[data-stage]')||event.button!==0)return;
  const svg=$('svg',target);if(!svg)return;
  const ratio=Math.max(svg.viewBox.baseVal.width/svg.getBoundingClientRect().width,svg.viewBox.baseVal.height/svg.getBoundingClientRect().height);
  graphDrag={x:event.clientX,y:event.clientY,start:{...state.pan},ratio,target};target.setPointerCapture(event.pointerId);
});
document.addEventListener('pointermove',event=>{if(graphDrag){state.pan={x:graphDrag.start.x+(event.clientX-graphDrag.x)*graphDrag.ratio,y:graphDrag.start.y+(event.clientY-graphDrag.y)*graphDrag.ratio};renderChain();}});
document.addEventListener('pointerup',()=>{graphDrag=null;});
document.addEventListener('pointercancel',()=>{graphDrag=null;});
document.addEventListener('dblclick',event=>{const target=event.target.closest('[data-stage]');if(target)chooseStage(target.dataset.stage,true);});
let resizeTimer;
window.addEventListener('resize',()=>{fitWorkbench();clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state.view==='analysis')renderAnalysis();},100);});
$('#modal').addEventListener('cancel',()=>{state.modal=null;});

async function boot() {
  $('#app').innerHTML=`<div class="loading">${icon('wave')}RF Link</div>`;
  try {
    const response=await fetch('/api/bootstrap');if(response.status===401){location.assign('/');return;}if(!response.ok)throw new Error('无法连接计算服务');
    config=await response.json();if(config.account)storedKey='rf-link-workbench-'+config.account.id;state.project=config.project;state.result=config.result;state.computed=fingerprint();state.directory=config.directory;
    let restored=false;
    try {
      const saved=JSON.parse(sessionStorage.getItem(storedKey) || 'null');
      if(['1.0.0','2.0.0'].includes(saved?.project?.schema_version)&&Array.isArray(saved.project.stages)&&saved.project.stages.length<=200){state.project=saved.project;state.selected=saved.selected;state.directory=saved.directory||config.directory;state.frequencyUnit=unitScales[saved.frequencyUnit]?saved.frequencyUnit:'MHz';state.bandwidthUnit=unitScales[saved.bandwidthUnit]?saved.bandwidthUnit:'MHz';state.result=null;state.computed=null;restored=true;}
    } catch(_) { /* Invalid browser draft never overwrites an on-disk project. */ }
    if(!currentStage())state.selected=state.project.stages[0]?.id || null;
    resetLabProject(state.project);if(state.project.lab?.circuit&&!state.project.stages.length){state.view='analysis';state.analysisTab='circuit';circuitUI.mode='edit';}mount();if(restored)await calculate();
  } catch(error) {$('#app').innerHTML=`<div class="loading">${esc(error.message)}</div>`;}
}
void boot();
