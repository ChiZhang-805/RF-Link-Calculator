'use strict';

const labUI = {kind:'frequency', benchmarkTarget:'chain', requests:{}, results:{}, busy:false, sweepMetric:'gain_db', spectrumMode:'tones', scalar:'ip3', waveMode:'predict', fit:null};
const labKinds = {frequency:'频率曲线',sparameter:'S 参数',ampm:'功率曲线',iq:'IQ 数据',imt:'混频杂散',reference:'参考结果',memory:'记忆模型',harmonic:'实射频多项式'};
const labTabs = [['sweep','扫频'],['spectrum','频谱'],['harmonic','谐波'],['circuit','电路'],['waveform','波形'],['benchmark','对标']];
function resetLabProject(project) {labUI.results={};labUI.requests={};labUI.fit=null;labUI.benchmarkTarget='chain';const refAnalysis=project.lab?.reference?.metadata?.analysis;if(refAnalysis){labUI.benchmarkTarget=refAnalysis.kind==='power'?'compression':['noise','image','image-auto','convergence'].includes(refAnalysis.kind)?'spectrum':refAnalysis.kind.startsWith('circuit-')?'circuit':refAnalysis.kind==='harmonic-convergence'?'harmonic':refAnalysis.kind;labUI.requests[labUI.benchmarkTarget]=copy(refAnalysis);}}
function modelBinding(stage,key) {
  const spec=state.project.lab?.models?.[stage.id] || {}, d=spec.linear;
  const active=!!spec.harmonic||(key==='gain_db'?!!(d||spec.nonlinear):key==='nf'?!!(d?.kind==='sparameter'||d?.nf_db||(d&&stage.noise.mode==='passive_thermal')):key==='iip3'?!!d?.iip3_dbm:key==='ip1'?!!(spec.nonlinear||d?.ip1_dbm):false);
  if(!active)return null;
  const metric={gain_db:'stage_gain_db',nf:'stage_nf_db',iip3:'stage_iip3_dbm',ip1:'stage_ip1_dbm'}[key];
  return {value:stale()?null:state.result?.stages.find(s=>s.stage_id===stage.id)?.metrics[metric]?.value ?? null};
}
function ensureLab() {state.project.schema_version='2.0.0';state.project.lab ||= {models:{},settings:{}};state.project.lab.models ||= {};state.project.lab.settings ||= {};return state.project.lab;}
function labSettings() {return state.project.lab?.settings || {};}
function labSetting(key,value,unit='',scale=1) {return inputBox((labSettings()[key] ?? value)/scale,unit,`data-lab-setting="${key}" data-scale="${scale}" aria-label="${esc(key)}"`);}
function labSelect(id,values,selected,label) {return `<select id="${id}" aria-label="${label}">${options(values,selected)}</select>`;}
function labButton(action,label,ic='play',extra='') {return `<button data-action="lab-${action}" ${extra}>${icon(ic==='open'?'folder':ic)}<span>${label}</span></button>`;}
function labResult(view=state.analysisTab) {const entry=labUI.results[view];return entry?.fingerprint===fingerprint()?entry.data:null;}
function labNumber(value,digits=5) {if(value==null||value==='')return '—';const n=Number(value);return n!==0&&(Math.abs(n)<.001||Math.abs(n)>=1e9)?n.toExponential(4):num(n,digits);}
function labScope(scope) {if(scope.startsWith('circuit:'))return scope.slice(8);if(scope==='chain')return '链路';if(scope==='waveform')return '波形';if(scope.startsWith('frequency:'))return num(Number(scope.slice(10))/1e6,6)+' MHz';if(scope.startsWith('input:'))return num(Number(scope.slice(6)),3)+' dBm';return state.project.stages.find(s=>s.id===scope)?.name||scope;}
function labMetric(row) {const names={phase_deg:'相位',wave_real:'波幅实部',wave_imag:'波幅虚部',voltage_v:'节点电压',dc_v:'直流电压',peak_v:'峰值电压',vbe_v:'VBE',vce_v:'VCE',ic_a:'IC',ib_a:'IB',voltage_real_v:'电压实部',voltage_imag_v:'电压虚部',voltage_noise_v2_hz:'电压噪声',noise_dbm_hz:'噪声功率密度',power_dbm:'输出功率',s21_real:'S21 实部',s21_imag:'S21 虚部',s11_db:'S11',s22_db:'S22',image_gain_db:'镜像增益',image_frequency_hz:'镜像频率'};const unit={'V2/Hz':'V²/Hz',sqrt_mW:'√mW',deg:'°',ratio:''}[row.unit]??row.unit;return (names[row.metric]||shortLabel(row.metric))+(unit?' / '+unit:'');}
function renderModelInspector(stage) {
  const slots=state.project.lab?.models?.[stage.id] || {};
  let body=field('数据类型',labSelect('model-kind',{frequency:labKinds.frequency,sparameter:labKinds.sparameter,ampm:labKinds.ampm,imt:labKinds.imt,harmonic:labKinds.harmonic},labUI.kind,'模型数据类型'));
  body+=`<div class="row lab-buttons">${labButton('model-import','导入','open')}${labButton('template','模板','export')}</div>`;
  for(const [slot,ds] of Object.entries(slots)) {
    const size=ds.frequency_hz?.length || ds.pin_dbm?.length || ds.m?.length || ds.coefficients?.length || 0;
    body+='<div class="field-rule"></div>'+field(slot==='linear'?'线性模型':slot==='nonlinear'?'非线性模型':slot==='harmonic'?'谐波模型':'混频模型',`<span>${esc(labKinds[ds.kind])}</span>`)+field('数据点',`<span class="num">${size}</span>`)+field('文件',`<span class="lab-filename" title="${esc(ds.metadata?.filename || '')}">${esc(ds.metadata?.filename || '—')}</span>`);
    if(ds.frequency_hz) body+=field('频率范围',`<span class="num">${num(ds.frequency_hz[0]/1e6)} — ${num(ds.frequency_hz.at(-1)/1e6)} MHz</span>`);
    if(ds.pin_dbm) body+=field('输入范围',`<span class="num">${num(ds.pin_dbm[0])} — ${num(ds.pin_dbm.at(-1))} dBm</span>`);
    if(ds.kind==='memory') body+=field('阶数',`<span>${ds.orders.at(-1)}</span>`)+field('记忆深度',`<span>${ds.depth}</span>`)+field('验证 NMSE',`<span>${num(ds.metadata?.validation?.nmse_db)} dB</span>`);
    body+=`<div class="row lab-buttons">${labButton('model-view','数据','table',`data-slot="${slot}"`)}${labButton('model-remove','移除','trash',`data-slot="${slot}"`)}</div>`;
  }
  return body;
}
function labControls(view) {
  if(view==='circuit')return circuitControls();
  const a=state.project.analysis;
  if(view==='sweep')return `<label>频率</label>${labSetting('sweep_low_hz',a.source_frequency_hz*.9,'MHz',1e6)}<span>—</span>${labSetting('sweep_high_hz',a.source_frequency_hz*1.1,'MHz',1e6)}${labButton('terminations','端口','sliders')}${labSelect('sweep-metric',{gain_db:'增益',nf_db:'噪声系数',phase_deg:'相位',s11_db:'S11',s22_db:'S22'},labUI.sweepMetric,'扫频指标')}${labButton('run','扫频')}`;
  if(view==='harmonic')return `<label>基频</label>${labSetting('hb_fundamental_hz',a.source_frequency_hz/10,'MHz',1e6)}<label>阶数</label>${labSelect('hb-harmonics',{16:'16',32:'32',64:'64',128:'128',256:'256',512:'512',1024:'1024'},labSettings().hb_harmonics||64,'谐波上限')}${labButton('terminations','端口','sliders')}${labButton('tones','载波','sliders')}${labButton('convergence','收敛')}${labButton('run','计算')}`;
  if(view==='spectrum')return `${labSelect('spectrum-mode',{tones:'双音 / 多音',imt:'混频杂散',image:'镜像终端','image-auto':'镜像噪声',noise:'带内噪声'},labUI.spectrumMode,'频谱模式')}${labUI.spectrumMode==='noise'?`${labSetting('noise_low_hz',a.source_frequency_hz-a.noise_bandwidth_hz/2,'MHz',1e6)}<span>—</span>${labSetting('noise_high_hz',a.source_frequency_hz+a.noise_bandwidth_hz/2,'MHz',1e6)}`:['image','image-auto'].includes(labUI.spectrumMode)?`${labUI.spectrumMode==='image'?labSetting('image_gain_db',-10,'dB'):''}${labSetting('image_temperature_k',290,'K')}`:`${labSelect('scalar-model',{ip3:'IP3',p1:'P1',linear:'线性'},labUI.scalar,'标量模型')}${labUI.spectrumMode==='tones'?labSelect('fft-count',{2048:'2048',4096:'4096',8192:'8192',16384:'16384'},labSettings().fft_count||4096,'频谱点数')+labSelect('oversampling',{1:'×1',2:'×2',4:'×4',8:'×8'},labSettings().oversampling||1,'过采样')+labButton('convergence','收敛')+labButton('tones','载波','sliders'):''}`}${labButton('run','计算')}`;
  if(view==='waveform')return `${labButton('iq-import','导入','open')}${labButton('fit-dialog','拟合','sliders')}${labSelect('wave-mode',{predict:'链路输出',measured:'实测输出'},labUI.waveMode,'波形数据')}${labButton('wave-settings','条件','sliders')}${labButton('run','计算')}`;
  return `${labSelect('benchmark-target',{chain:'链路',compression:'压缩',sweep:'扫频',spectrum:'频谱',harmonic:'谐波',circuit:'电路',waveform:'波形'},labUI.benchmarkTarget,'对标对象')}${labButton('handoff','导出算例','export')}${labButton('reference-import','导入参考','open')}<label>容差</label>${labSetting('tolerance_db',.001,'dB')}${labButton('run','对比')}`;
}
function labTable(headers,rows) {return `<div class="analysis-table-wrap hide-scroll lab-table"><table class="analysis-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}
function labStats(values) {return `<div class="lab-stats">${values.map(([label,value,unit])=>`<div class="analysis-stat"><span>${esc(label)}</span><strong>${num(value,3)} <small>${esc(unit||'')}</small></strong></div>`).join('')}</div>`;}
function renderLabAnalysis(target) {
  const view=state.analysisTab, result=labResult();
  target.innerHTML=`<div class="lab-controls">${labControls(view)}<span class="spacer"></span>${labButton('result-export','结果','export',result?'':'disabled')}</div><div class="lab-content" id="lab-content"></div>`;
  const content=$('#lab-content');
  if(labUI.busy){content.innerHTML='<div class="empty">计算中</div>';return;}
  if(view==='circuit'){renderCircuit(content,result);return;}
  if(!result){content.innerHTML=`<div class="empty">${labButton('run',view==='benchmark'?'对比':'计算')}</div>`;return;}
  if(view==='sweep') {
    const key=labUI.sweepMetric;
    content.innerHTML=`<div id="plot" class="chart-wrap lab-chart"></div>`+labTable(['频率 / MHz','增益 / dB','NF / dB','相位 / °'],result.points.map(p=>`<tr><td>${num(p.frequency_hz/1e6,4)}</td><td>${num(p.gain_db,4)}</td><td>${num(p.nf_db,4)}</td><td>${num(p.phase_deg,3)}</td></tr>`));
    requestAnimationFrame(()=>plot([{label:{gain_db:'增益',nf_db:'NF',phase_deg:'相位',s11_db:'S11',s22_db:'S22'}[key],color:'#087f8c',points:result.points.map(p=>({x:p.frequency_hz/1e6,y:p[key]??null}))}],{x:'MHz',y:key==='phase_deg'?'°':'dB'}));
  } else if(view==='spectrum'||view==='harmonic') {
    if(result.noise_output_dbm!=null){content.innerHTML=labStats([['镜像频率',result.image_frequency_hz==null?null:result.image_frequency_hz/1e6,'MHz'],['镜像增益',result.image_gain_db,'dB'],['噪声系数',result.nf_db,'dB'],['镜像温度',result.image_temperature_k,'K'],['等效噪温',result.equivalent_noise_temperature_k,'K'],['输出噪声',result.noise_output_dbm,'dBm']]);return;}
    content.innerHTML=(result.solver?`<div class="row lab-summary"><strong>${result.solver.status==='converged'?'已收敛':'未收敛'}</strong><span>残差 ${result.solver.relative_residual.toExponential(2)}</span><span>迭代 ${result.solver.iterations}</span><span>阶数 ${result.harmonics}</span></div>`:'')+(result.convergence?`<div class="row lab-summary"><strong>收敛${result.convergence.status==='pass'?'通过':'未通过'}</strong></div>`:'')+(result.integrated_noise_dbm!=null?labStats([['带内噪声',result.integrated_noise_dbm,'dBm'],['带宽',result.bandwidth_hz/1e6,'MHz']]):'')+`<div id="plot" class="chart-wrap lab-chart"></div>`+labTable(result.integrated_noise_dbm!=null?['频率 / MHz','噪声 / dBm/Hz']:['频率 / MHz','输出 / dBm','分量','路径'],result.points.map((p,i)=>`<tr><td>${num(p.frequency_hz/1e6,6)}</td><td>${num(p.power_dbm,3)}</td>${result.integrated_noise_dbm!=null?'':`<td>${esc(p.product || '—')}</td><td>${p.path?.length?`<button class="icon ghost" data-action="lab-path" data-index="${i}" aria-label="分量路径">${icon('right')}</button>`:'—'}</td>`}</tr>`));
    requestAnimationFrame(()=>plot([{label:'输出',color:'#087f8c',points:result.points.map(p=>({x:p.frequency_hz/1e6,y:p.power_dbm}))}],{x:'MHz',y:result.integrated_noise_dbm!=null?'dBm/Hz':'dBm',stems:result.integrated_noise_dbm==null}));
  } else if(view==='waveform') {
    const m=result.metrics;
    content.innerHTML=`<div id="plot" class="chart-wrap lab-chart"></div>`+labStats([['输入功率',m.input_dbm,'dBm'],['输出功率',m.output_dbm,'dBm'],['波形 EVM',m.evm_gain_aligned_percent,'%'],['左邻道',m.acpr_left_dbc,'dBc'],['右邻道',m.acpr_right_dbc,'dBc'],['预测 NMSE',result.validation?.nmse_db,'dB']]);
    requestAnimationFrame(()=>plot([{label:'频谱',color:'#087f8c',points:result.points.map(p=>({x:p.frequency_hz/1e6,y:p.power_dbm}))}],{x:'MHz',y:'dBm / bin'}));
  } else {
    const labels={published_match:'公开算例吻合',pass:'通过',fail:'超差',missing:'缺失',unavailable:'未知',mismatch:'口径不符',internal_only:'内部自检',incomplete_or_failed:'未通过'};
    content.innerHTML=`<div class="row lab-summary"><strong>${labels[result.status] || '待验证'}</strong><span>${esc(result.metadata.software)}</span></div>`+labTable(['位置','指标','本地','参考','差值','结果'],result.rows.map(r=>`<tr><td>${esc(labScope(r.scope))}</td><td>${esc(labMetric(r))}</td><td>${labNumber(r.value)}</td><td>${labNumber(r.reference)}</td><td>${labNumber(r.delta,6)}</td><td>${esc(labels[r.comparison])}</td></tr>`));
  }
}
function labImportDialog(kind) {
  labUI.importKind=kind;labUI.importStage=state.selected;
  let body=field('数据类型',`<span>${esc(labKinds[kind])}</span>`);
  if(kind==='reference') {
    for(const [key,label] of [['software','软件'],['version','版本'],['solver','求解器'],['calculation_hash','输入散列']])body+=field(label,`<input id="meta-${key}" aria-label="${label}" value="" maxlength="256">`);
    body+=field('来源',labSelect('meta-source',{commercial:'商用软件',measurement:'独立测量',independent:'独立实现',published:'公开算例',synthetic:'内部自检'},'commercial','参考来源'));
  } else {
    body+=field('来源',`<input id="meta-source" aria-label="数据来源" maxlength="1024">`)+field('版本',`<input id="meta-version" aria-label="数据版本" maxlength="256">`);
    if(kind==='harmonic')for(const [key,label,unit,value] of [['max_input','输入峰值','√mW',1],['pole_hz','极点频率','Hz',0],['s11','S11 实部','',0],['s12','S12 实部','',0],['s22','S22 实部','',0]])body+=field(label,inputBox(value,unit,`id="meta-${key}" aria-label="${label}"`));
    if(kind==='imt')for(const [key,label,unit,value] of [['rf_hz','RF频率','Hz',state.project.analysis.source_frequency_hz],['lo_hz','LO频率','Hz',currentStage()?.mixer?.lo_frequency_hz],['rf_dbm','RF功率','dBm',state.project.analysis.input_power_dbm],['lo_dbm','LO功率','dBm',currentStage()?.mixer?.lo_power_dbm]])body+=field(label,inputBox(value,unit,`id="meta-${key}" aria-label="${label}"`));
  }
  openModal('导入'+labKinds[kind],body,`${kind!=='reference'&&kind!=='sparameter'?labButton('import-template','模板','export'):''}${labButton('pick-file','选择文件','open')}`,'lab-import');
}
function labWaveSettings(fit=false) {
  let body=field('采样率',labSetting('sample_rate_hz',10e6,'MHz',1e6));
  if(fit) {
    body+=field('目标器件',labSelect('fit-stage',Object.fromEntries(state.project.stages.map(s=>[s.id,s.name])),state.selected,'拟合器件'))+field('阶数',labSelect('fit-order',{1:'1',3:'3',5:'5',7:'7',9:'9'},5,'多项式阶数'))+field('记忆深度',inputBox(3,'', 'id="fit-depth" min="1" max="8" aria-label="记忆深度"'))+field('对齐延迟',inputBox(0,'采样点','id="fit-delay" min="0" max="1024" aria-label="对齐延迟"'))+field('正则系数',inputBox(0,'','id="fit-ridge" min="0" max="1" aria-label="正则系数"'));
    if(labUI.fit)body+=labStats([['训练 NMSE',labUI.fit.training.nmse_db,'dB'],['验证 NMSE',labUI.fit.validation.nmse_db,'dB']]);
  } else body+=field('信道带宽',labSetting('channel_bandwidth_hz',1e6,'MHz',1e6))+field('邻道间隔',labSetting('channel_spacing_hz',2e6,'MHz',1e6))+field('标量模型',labSelect('scalar-model',{ip3:'IP3',p1:'P1',linear:'线性'},labUI.scalar,'标量模型'));
  openModal(fit?'记忆模型':'波形条件',body,fit?labButton('fit','拟合'):'<button data-action="close">完成</button>',fit?'lab-fit':'lab-wave');
}
async function labDownload(action,data,name) {
  const response=await api('lab-'+action,{project:state.project,...data},true);
  const blob=await response.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
}
async function labAction(name,target=null) {
  if(name.startsWith('circuit-')){await circuitAction(name.slice(8),target);return;}
  if(name==='model-import'){labUI.kind=$('#model-kind').value;labImportDialog(labUI.kind);return;}
  if(name==='iq-import'||name==='reference-import'){labImportDialog(name==='iq-import'?'iq':'reference');return;}
  if(name==='template'||name==='import-template'){const kind=name==='template'?$('#model-kind').value:labUI.importKind;await labDownload('template',{kind},labKinds[kind]+'_模板.csv');return;}
  if(name==='pick-file') {
    const metadata={};if(labUI.importKind==='reference'&&labUI.benchmarkTarget!=='chain'){if(!labUI.requests[labUI.benchmarkTarget]){modalError('请先计算所选分析');return;}metadata.analysis=labUI.requests[labUI.benchmarkTarget];}for(const input of $$('[id^="meta-"]'))metadata[input.id.slice(5)]=input.type==='number'?Number(input.value):input.value.trim();
    if(labUI.importKind==='reference'&&['software','version','solver','calculation_hash'].some(k=>!metadata[k])){modalError('请填写参考信息');return;}
    labUI.importMetadata=metadata;$('#lab-file-input').accept=labUI.importKind==='sparameter'?'.s2p,.ts':'.csv';$('#lab-file-input').click();return;
  }
  if(name==='model-remove'){checkpoint();delete ensureLab().models[state.selected][target.dataset.slot];changed({inspector:true});return;}
  if(name==='model-view') {
    const ds=state.project.lab.models[state.selected][target.dataset.slot];
    const arrays=Object.entries(ds).filter(([k,v])=>Array.isArray(v));const count=Math.max(...arrays.map(([,v])=>v.length));
    openModal(labKinds[ds.kind],labTable(['序号',...arrays.map(([k])=>k)],Array.from({length:Math.min(count,100)},(_,i)=>`<tr><td>${i+1}</td>${arrays.map(([,v])=>`<td>${esc(Array.isArray(v[i])?JSON.stringify(v[i]):v[i]??'—')}</td>`).join('')}</tr>`)),labButton('dataset-export','导出','export',`data-slot="${target.dataset.slot}"`));return;
  }
  if(name==='dataset-export') {const ds=state.project.lab.models[state.selected][target.dataset.slot];labSaveData(ds,'器件模型.json');return;}
  if(name==='terminations') {
    let body='';for(const [key,label] of [['source_gamma','源反射'],['load_gamma','负载反射']])for(let i=0;i<2;i++)body+=field(label+(i?'虚部':'实部'),inputBox(labSettings()[key]?.[i]||0,'',`data-lab-gamma="${key}" data-component="${i}" aria-label="${label}${i?'虚部':'实部'}"`));
    openModal('端口',body,'<button data-action="close">完成</button>');return;
  }
  if(name==='wave-settings'||name==='fit-dialog'){labWaveSettings(name==='fit-dialog');return;}
  if(name==='tones') {
    const a=state.project.analysis,tones=labSettings().tones || [{offset_hz:-a.two_tone.spacing_hz/2,power_dbm:a.two_tone.each_tone_power_dbm,phase_deg:0},{offset_hz:a.two_tone.spacing_hz/2,power_dbm:a.two_tone.each_tone_power_dbm,phase_deg:0}];
    labUI.editTones=copy(tones);renderTonesDialog();return;
  }
  if(name==='tone-add'){if(labUI.editTones.length<16)labUI.editTones.push({offset_hz:0,power_dbm:-50,phase_deg:0});renderTonesDialog();return;}
  if(name==='tone-delete'){labUI.editTones.splice(Number(target.dataset.index),1);renderTonesDialog();return;}
  if(name==='tone-save'){checkpoint();ensureLab().settings.tones=copy(labUI.editTones);closeModal();changed();return;}
  if(name==='tone-reset'){checkpoint();delete ensureLab().settings.tones;closeModal();changed();return;}
  if(name==='path'){const p=labResult().points[Number(target.dataset.index)];openModal('分量路径',labTable(['器件',labResult().solver?'前向功率 / dBm':'输出 / dBm'],p.path.map(row=>`<tr><td>${esc(state.project.stages.find(s=>s.id===row.stage_id)?.name||row.stage_id)}</td><td>${num(row.power_dbm,4)}</td></tr>`)));return;}
  if(name==='handoff'){const analysis=labUI.benchmarkTarget==='chain'?null:labUI.requests[labUI.benchmarkTarget];if(labUI.benchmarkTarget!=='chain'&&!analysis)throw new Error('请先计算所选分析');await labDownload('handoff',{analysis},'外部对标.zip');return;}
  if(name==='result-export'){labSaveData(labResult(),state.analysisTab+'_结果.json');return;}
  if(name==='fit') {
    const data={stage_id:$('#fit-stage').value,sample_rate_hz:labSettings().sample_rate_hz||10e6,order:Number($('#fit-order').value),depth:Number($('#fit-depth').value),delay:Number($('#fit-delay').value),ridge:Number($('#fit-ridge').value)};
    const stamp=fingerprint();const response=await api('lab-fit',{project:state.project,...data});if(stamp!==fingerprint())return;
    checkpoint();state.project=response.project;labUI.fit=response.fit;changed();labWaveSettings(true);return;
  }
  if(name==='run'||name==='convergence') {
    if(labUI.busy)return;
    const view=state.analysisTab,s=labSettings(),a=state.project.analysis;let action=view,data={};
    if(view==='sweep'){data={low:s.sweep_low_hz??a.source_frequency_hz*.9,high:s.sweep_high_hz??a.source_frequency_hz*1.1,count:201};}
    if(view==='spectrum'){if(labUI.spectrumMode==='noise'){action='noise';data={low:s.noise_low_hz??a.source_frequency_hz-a.noise_bandwidth_hz/2,high:s.noise_high_hz??a.source_frequency_hz+a.noise_bandwidth_hz/2};}else if(['image','image-auto'].includes(labUI.spectrumMode)){action=labUI.spectrumMode;data={image_gain_db:s.image_gain_db??-10,image_temperature_k:s.image_temperature_k??290};}else data={mode:labUI.spectrumMode,count:Number($('#fft-count')?.value||s.fft_count||4096),scalar_model:labUI.scalar,tones:s.tones,oversampling:s.oversampling||1};}
    if(view==='harmonic'){const tones=s.tones?.map(t=>({...t,frequency_hz:a.source_frequency_hz+t.offset_hz}))||(a.two_tone.enabled?[-.5,.5].map(k=>({frequency_hz:a.source_frequency_hz+k*a.two_tone.spacing_hz,power_dbm:a.two_tone.each_tone_power_dbm})):undefined);data={fundamental_hz:s.hb_fundamental_hz??a.source_frequency_hz/10,harmonics:s.hb_harmonics||64,tones};}
    if(view==='circuit'){const request=circuitRequest(name==='convergence');if(!request)return;action=request.action;data=request.data;}
    if(view==='waveform'){data={sample_rate_hz:s.sample_rate_hz||10e6,bandwidth_hz:s.channel_bandwidth_hz||1e6,spacing_hz:s.channel_spacing_hz||2e6,mode:labUI.waveMode,scalar_model:labUI.scalar};}
    if(view==='benchmark'){const expected=labUI.benchmarkTarget==='compression'?'power':labUI.benchmarkTarget==='chain'?null:labUI.requests[labUI.benchmarkTarget]?.kind||labUI.benchmarkTarget;const actual=state.project.lab?.reference?.metadata?.analysis?.kind||null;if(expected!==actual)throw new Error('参考结果与对标对象不一致');action='compare';data={tolerance_db:s.tolerance_db??.001};}
    if(name==='convergence'&&view!=='circuit')action=view==='harmonic'?'harmonic-convergence':'convergence';if(view!=='benchmark')labUI.requests[view]={kind:action,parameters:copy(data)};const stamp=fingerprint();labUI.busy=true;renderAnalysis();
    try{const result=await api('lab-'+action,{project:state.project,...data});if(stamp===fingerprint())labUI.results[view]={data:result,fingerprint:stamp};}
    finally{labUI.busy=false;updateChrome();if(state.view==='analysis')renderAnalysis();}
  }
}
function renderTonesDialog() {
  const body=`<div class="lab-tones"><table class="analysis-table"><thead><tr><th>偏移 / Hz</th><th>功率 / dBm</th><th>相位 / °</th><th></th></tr></thead><tbody>${labUI.editTones.map((t,i)=>`<tr>${['offset_hz','power_dbm','phase_deg'].map(k=>`<td><input type="number" step="any" data-tone-index="${i}" data-tone-key="${k}" aria-label="${k}" value="${t[k]}"></td>`).join('')}<td><button class="icon ghost" data-action="lab-tone-delete" data-index="${i}" aria-label="删除载波">${icon('trash')}</button></td></tr>`).join('')}</tbody></table></div>`;
  openModal('载波',body,`${labButton('tone-add','添加','plus')}${labButton('tone-reset','双音','undo')}${labButton('tone-save','保存','save')}`);
}
function labSaveData(data,name) {const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
document.addEventListener('change',event=>{
  const el=event.target;if(typeof state==='undefined'||!state.project)return;
  if(el.dataset.labSetting){checkpoint();ensureLab().settings[el.dataset.labSetting]=Number(el.value)*Number(el.dataset.scale||1);changed({table:false});return;}
  if(el.dataset.labGamma){checkpoint();const s=ensureLab().settings;s[el.dataset.labGamma] ||= [0,0];s[el.dataset.labGamma][Number(el.dataset.component)]=Number(el.value);changed({table:false});return;}
  if(el.hasAttribute('data-tone-index')){labUI.editTones[Number(el.dataset.toneIndex)][el.dataset.toneKey]=Number(el.value);return;}
  const choices={'sweep-metric':'sweepMetric','spectrum-mode':'spectrumMode','scalar-model':'scalar','wave-mode':'waveMode','model-kind':'kind','benchmark-target':'benchmarkTarget'};
  if(choices[el.id]){labUI[choices[el.id]]=el.value;if(['spectrum-mode','scalar-model'].includes(el.id)){delete labUI.results.spectrum;delete labUI.results.waveform;}if(el.id==='wave-mode')delete labUI.results.waveform;if(el.id==='benchmark-target')delete labUI.results.benchmark;if(el.id!=='model-kind'&&!$('#modal').open)renderAnalysis();}
  if(el.id==='hb-harmonics'){checkpoint();ensureLab().settings.hb_harmonics=Number(el.value);changed({table:false});}
  if(el.id==='fft-count'||el.id==='oversampling'){checkpoint();ensureLab().settings[el.id==='fft-count'?'fft_count':'oversampling']=Number(el.value);changed({table:false});}
});
document.addEventListener('DOMContentLoaded',()=>{
  $('#lab-file-input').addEventListener('change',async event=>{
    const file=event.target.files[0];if(!file)return;
    try {
      if(file.size>8*1024*1024)throw new Error('数据文件过大');
      const stamp=fingerprint();const response=await api('lab-import',{project:state.project,kind:labUI.importKind,stage_id:labUI.importStage,text:await file.text(),filename:file.name,metadata:labUI.importMetadata});
      if(stamp!==fingerprint())return;
      checkpoint();state.project=response.project;closeModal();changed({inspector:true});toast('已导入');
    }catch(error){modalError(error);}finally{event.target.value='';}
  });
});
