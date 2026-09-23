'use strict';

const circuitUI={mode:'edit',selected:0,templates:null};
const circuitTypes={resistor:'电阻',capacitor:'电容',voltage:'电压源',npn:'NPN'};
function circuitSpec(){return state.project.lab?.circuit;}
function circuitButton(action,label,ic='sliders',extra=''){return labButton('circuit-'+action,label,ic,extra);}
function circuitField(key,label,value,unit='',attrs=''){
  return field(label,typeof value==='number'?inputBox(value,unit,`data-circuit-field="${key}" aria-label="${label}" ${attrs}`):`<input data-circuit-field="${key}" aria-label="${label}" value="${esc(value)}" ${attrs}>`);
}
function circuitControls(){
  let body=labSelect('circuit-mode',{edit:'元件',dc:'工作点',hb:'谐波',noise:'周期噪声'},circuitUI.mode,'电路分析');
  body+=circuitButton('template','新建','plus')+circuitButton('import','导入','open')+circuitButton('export','电路','export',circuitSpec()?'':'disabled');
  if(circuitUI.mode==='edit')return body+circuitButton('add','元件','plus',circuitSpec()?'':'disabled');
  if(circuitUI.mode!=='dc')body+=`<label>基频</label>${labSetting('circuit_base_hz',1e6,'MHz',1e6)}<label>阶数</label>${labSelect('circuit-harmonics',{2:'2',4:'4',8:'8',16:'16',32:'32'},labSettings().circuit_harmonics||8,'电路谐波阶数')}`;
  if(circuitUI.mode==='noise')body+=circuitButton('noise-settings','噪声','sliders');
  return body+(circuitUI.mode!=='dc'?labButton('convergence','收敛'):'')+labButton('run','计算');
}
function renderCircuit(content,result){
  const c=circuitSpec();
  if(!c){content.innerHTML=`<div class="empty">${circuitButton('template','新建电路','plus')}${circuitButton('import','导入','open')}</div>`;return;}
  if(circuitUI.mode==='edit'){
    circuitUI.selected=Math.min(circuitUI.selected,c.components.length-1);
    const selected=c.components[circuitUI.selected];
    let body=field('输出电阻',labSelect('circuit-output',Object.fromEntries(c.components.filter(e=>e.type==='resistor').map(e=>[e.id,e.id])),c.output_resistor,'输出电阻'))+field('温度',inputBox(c.temperature_k,'K','id="circuit-temperature" aria-label="电路温度"'));
    if(selected){
      body+='<div class="field-rule"></div>'+circuitField('id','名称',selected.id);
      for(const pin of selected.type==='npn'?['b','c','e']:['p','n'])body+=circuitField(pin,{b:'基极',c:'集电极',e:'发射极',p:'正端',n:'负端'}[pin],selected[pin]);
      if(selected.type==='resistor'||selected.type==='capacitor')body+=circuitField('value',selected.type==='resistor'?'阻值':'电容',selected.value,selected.type==='resistor'?'Ω':'F');
      if(selected.type==='resistor')body+=circuitField('temperature_k','噪声温度',selected.temperature_k??c.temperature_k,'K');
      if(selected.type==='voltage'){
        body+=circuitField('dc_v','直流电压',selected.dc_v,'V');
        selected.tones.forEach((t,i)=>{body+='<div class="field-rule"></div>';for(const [key,label,unit] of [['harmonic','谐波序号',''],['peak_v','峰值','V'],['phase_deg','相位','°']])body+=field(label,inputBox(t[key],unit,`data-circuit-tone="${i}" data-key="${key}" aria-label="载波${i+1}${label}"`));body+=circuitButton('tone-remove','删除载波','trash',`data-index="${i}"`);});
        body+=`<div class="row lab-buttons">${circuitButton('tone-add','载波','plus')}</div>`;
      }
      if(selected.type==='npn')body+=field('模型',labSelect('circuit-model',Object.fromEntries(Object.keys(c.models).map(k=>[k,k])),selected.model,'晶体管模型'))+circuitButton('model','模型参数','sliders');
      body+=`<div class="field-rule"></div>${circuitButton('remove','删除元件','trash')}`;
    }
    const table=labTable(['元件','类型','节点','参数'],c.components.map((e,i)=>`<tr class="${i===circuitUI.selected?'selected':''}"><td><button class="ghost" data-action="lab-circuit-select" data-index="${i}">${esc(e.id)}</button></td><td>${circuitTypes[e.type]}</td><td>${esc((e.type==='npn'?[e.b,e.c,e.e]:[e.p,e.n]).join(' · '))}</td><td>${e.type==='npn'?esc(e.model):labNumber(e.value??e.dc_v)} ${e.type==='resistor'?'Ω':e.type==='capacitor'?'F':e.type==='voltage'?'V':''}</td></tr>`));
    content.innerHTML=`<div class="circuit-editor">${table}<aside class="circuit-inspector hide-scroll">${body}</aside></div>`;
    return;
  }
  if(!result){content.innerHTML=`<div class="empty">${labButton('run','计算')}</div>`;return;}
  const solver=result.solver||result.operating_point?.solver;
  const summary=`<div class="row lab-summary"><strong>已收敛</strong><span>残差 ${solver.residual_a.toExponential(2)} A</span>${result.stability?`<span>${result.stability.status==='stable'?'稳定':'代数解'}</span>`:''}${result.convergence?`<strong>截断${result.convergence.status==='pass'?'通过':'未通过'}</strong>`:''}</div>`;
  if(circuitUI.mode==='dc'){
    content.innerHTML=summary+labTable(['节点','电压 / V'],result.nodes.map(r=>`<tr><td>${esc(r.node)}</td><td>${labNumber(r.voltage_v)}</td></tr>`))+labTable(['晶体管','VBE / V','VCE / V','IC / A','IB / A'],result.devices.map(r=>`<tr><td>${esc(r.id)}</td><td>${labNumber(r.vbe_v)}</td><td>${labNumber(r.vce_v)}</td><td>${labNumber(r.ic_a)}</td><td>${labNumber(r.ib_a)}</td></tr>`));return;
  }
  const noise=circuitUI.mode==='noise',points=[...result.points].sort((a,b)=>a.frequency_hz-b.frequency_hz);
  content.innerHTML=summary+`<div id="plot" class="chart-wrap lab-chart"></div>`+labTable(noise?['频率 / MHz','噪声 / dBm/Hz','电压噪声 / V²/Hz','贡献']:['频率 / MHz','功率 / dBm','电压实部 / V','电压虚部 / V'],points.map(p=>`<tr><td>${num(p.frequency_hz/1e6,6)}</td><td>${num(noise?p.noise_dbm_hz:p.power_dbm,4)}</td><td>${labNumber(noise?p.voltage_noise_v2_hz:p.voltage_real_v)}</td><td>${noise?circuitButton('contributions','查看','right',`data-frequency="${p.frequency_hz}"`):labNumber(p.voltage_imag_v)}</td></tr>`));
  requestAnimationFrame(()=>plot([{label:noise?'噪声':'输出',color:'#087f8c',points:points.filter(p=>noise||p.frequency_hz>0&&p.power_dbm>Math.max(...points.filter(p=>p.frequency_hz>0).map(p=>p.power_dbm))-120).map(p=>({x:p.frequency_hz/1e6,y:noise?p.noise_dbm_hz:p.power_dbm}))}],{x:'MHz',y:noise?'dBm/Hz':'dBm',stems:!noise,scatter:noise}));
}
function circuitRequest(convergence){
  const s=labSettings(),mode=circuitUI.mode;
  if(mode==='edit')return null;
  return {action:'circuit-'+mode+(convergence&&mode!=='dc'?'-convergence':''),data:mode==='dc'?{}:{fundamental_hz:s.circuit_base_hz??1e6,harmonics:s.circuit_harmonics??8,...(mode==='noise'?{offset_hz:s.circuit_offset_hz??1e4,sidebands:s.circuit_sidebands??8,output_harmonics:s.circuit_output_harmonics??1}:{})}};
}
async function circuitAction(action,target){
  const c=circuitSpec(),selected=c?.components[circuitUI.selected];
  if(action==='select'){circuitUI.selected=Number(target.dataset.index);renderAnalysis();return;}
  if(action==='template'){
    circuitUI.templates=(await api('lab-circuit-template',{project:state.project})).circuits;
    openModal('新建电路',field('模板',labSelect('circuit-template',Object.fromEntries(Object.keys(circuitUI.templates).map(k=>[k,k])),Object.keys(circuitUI.templates)[0],'电路模板')),circuitButton('create','创建','plus'));return;
  }
  if(action==='create'){checkpoint();ensureLab().circuit=copy(circuitUI.templates[$('#circuit-template').value]);circuitUI.selected=0;circuitUI.mode='edit';closeModal();changed();return;}
  if(action==='import'){$('#circuit-file-input').click();return;}
  if(action==='handoff'){const analysis=labUI.requests.circuit;if(!analysis||!labResult('circuit'))throw new Error('请先计算电路');await labDownload('handoff',{analysis},'电路对标.zip');return;}
  if(action==='spice'){await labDownload('circuit-spice',{fundamental_hz:labSettings().circuit_base_hz??1e6},'电路.cir');return;}
  if(action==='export-dialog'){openModal('导出电路',circuitButton('export','电路','export')+labButton('result-export','结果','export',labResult()?'':'disabled')+circuitButton('spice','网表','export')+circuitButton('handoff','对标工程','export',labResult('circuit')?'':'disabled'));return;}
  if(action==='export'){labSaveData(c,'电路.json');return;}
  if(action==='noise-settings'){openModal('周期噪声',field('频率偏移',labSetting('circuit_offset_hz',1e4,'kHz',1e3))+field('边带数',labSelect('circuit-sidebands',{2:'2',4:'4',8:'8',16:'16',32:'32'},labSettings().circuit_sidebands??8,'噪声边带数'))+field('观测阶数',labSelect('circuit-output-harmonics',{0:'0',1:'1',2:'2',4:'4',8:'8',16:'16'},labSettings().circuit_output_harmonics??1,'噪声观测阶数')),'<button data-action="close">完成</button>');return;}
  if(action==='contributions'){const p=labResult().points.find(p=>p.frequency_hz===Number(target.dataset.frequency));openModal('噪声贡献',labTable(['噪声源','电压噪声 / V²/Hz'],p.contributions.map(r=>`<tr><td>${esc(r.source.replace(':base',' · 基极').replace(':collector',' · 集电极'))}</td><td>${labNumber(r.voltage_noise_v2_hz)}</td></tr>`)));return;}
  if(action==='add'){openModal('添加元件',field('类型',labSelect('circuit-add-type',circuitTypes,'resistor','元件类型')),circuitButton('add-confirm','添加','plus'));return;}
  if(action==='add-confirm'){
    const type=$('#circuit-add-type').value;let id={resistor:'R',capacitor:'C',voltage:'V',npn:'Q'}[type],i=1;while(c.components.some(e=>e.id.toLowerCase()===(id+i).toLowerCase()))i++;id+=i;
    checkpoint();let e={id,type,p:'out',n:'0',value:type==='resistor'?1000:1e-12};
    if(type==='voltage')e={id,type,p:'source'+i,n:'0',dc_v:0,tones:[]};
    if(type==='npn'){
      if(!Object.keys(c.models).length){const templates=(await api('lab-circuit-template',{project:state.project})).circuits;c.models=copy(templates['NPN放大电路'].models);}
      e={id,type,b:'b',c:'c',e:'0',model:Object.keys(c.models)[0]};
    }
    c.components.push(e);circuitUI.selected=c.components.length-1;closeModal();changed();return;
  }
  if(action==='remove'){checkpoint();c.components.splice(circuitUI.selected,1);circuitUI.selected=Math.max(0,circuitUI.selected-1);changed();return;}
  if(action==='tone-add'){if(selected.tones.length>=8)return;checkpoint();let h=1;while(selected.tones.some(t=>t.harmonic===h))h++;selected.tones.push({harmonic:h,peak_v:.01,phase_deg:0});changed();return;}
  if(action==='tone-remove'){checkpoint();selected.tones.splice(Number(target.dataset.index),1);changed();return;}
  if(action==='model'){
    const m=c.models[selected.model];let body='';for(const [key,label,unit] of [['is_a','饱和电流','A'],['bf','正向 β',''],['br','反向 β',''],['cbe_f','CBE','F'],['cbc_f','CBC','F'],['max_current_a','电流上限','A'],['max_vbe_v','VBE 上限','V'],['max_vce_v','结耐压','V']])body+=field(label,inputBox(m[key],unit,`data-circuit-model-field="${key}" aria-label="${label}"`));openModal('NPN · '+selected.model,body,'<button data-action="close">完成</button>');
  }
}
document.addEventListener('change',event=>{
  if(typeof state==='undefined'||!state.project)return;
  const el=event.target,c=circuitSpec(),selected=c?.components[circuitUI.selected];
  if(el.id==='circuit-mode'){circuitUI.mode=el.value;delete labUI.results.circuit;delete labUI.requests.circuit;renderAnalysis();return;}
  const settings={'circuit-harmonics':'circuit_harmonics','circuit-sidebands':'circuit_sidebands','circuit-output-harmonics':'circuit_output_harmonics'};
  if(settings[el.id]){checkpoint();ensureLab().settings[settings[el.id]]=Number(el.value);changed();return;}
  if(el.dataset.circuitField){checkpoint();const key=el.dataset.circuitField,old=selected[key];selected[key]=el.type==='number'?Number(el.value):el.value.trim();if(key==='id'&&c.output_resistor===old)c.output_resistor=selected.id;changed();return;}
  if(el.hasAttribute('data-circuit-tone')){checkpoint();selected.tones[Number(el.dataset.circuitTone)][el.dataset.key]=Number(el.value);changed();return;}
  if(el.dataset.circuitModelField){checkpoint();c.models[selected.model][el.dataset.circuitModelField]=Number(el.value);changed();return;}
  if(['circuit-output','circuit-temperature','circuit-model'].includes(el.id)){checkpoint();if(el.id==='circuit-output')c.output_resistor=el.value;else if(el.id==='circuit-temperature')c.temperature_k=Number(el.value);else selected.model=el.value;changed();}
});
document.addEventListener('DOMContentLoaded',()=>{
  $('#circuit-file-input').addEventListener('change',async event=>{
    const file=event.target.files[0];if(!file)return;const stamp=fingerprint();
    try{if(file.size>512*1024)throw new Error('电路文件过大');const r=await api('lab-circuit-import',{project:state.project,text:await file.text()});if(stamp!==fingerprint())return;checkpoint();state.project=r.project;circuitUI.selected=0;circuitUI.mode='edit';changed();toast('已导入');}catch(error){toast(error.message,true);}finally{event.target.value='';}
  });
});
