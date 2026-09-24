'use strict';
const el=id=>document.getElementById(id);
let hosted=false,mode='login',token='';
function showMode(next){
  mode=next;el('message').textContent='';el('message').className='';
  const titles={login:'登录',register:'注册',forgot:'找回密码',resend:'重发验证邮件',reset:'重置密码',verify:'验证邮箱'};
  el('form-title').textContent=titles[mode];el('submit').textContent=titles[mode];
  const needEmail=['login','register','forgot','resend'].includes(mode),needPassword=['login','register','reset'].includes(mode)||(mode==='forgot'&&!hosted);
  el('email-row').hidden=!needEmail;el('email').required=needEmail;
  el('code-row').hidden=mode!=='register';el('code').required=mode==='register'&&hosted;el('code').value='';
  el('password-row').hidden=!needPassword;el('password').required=needPassword;el('password').value='';el('password').autocomplete=mode==='login'?'current-password':'new-password';
  el('confirm-row').hidden=!needPassword||mode==='login';el('confirm').required=needPassword&&mode!=='login';el('confirm').value='';
  el('key-row').hidden=!(mode==='forgot'&&!hosted);el('key').required=!el('key-row').hidden;
  el('recovery-row').hidden=true;el('submit').hidden=false;el('back').hidden=true;el('resend').hidden=!hosted;
  el('auth-tools').hidden=mode!=='login';
  const simple=['login','register'].includes(mode);
  document.querySelectorAll('.mode-tab').forEach(b=>{b.hidden=!simple;b.classList.toggle('active',b.dataset.mode===mode);});
  el('nav-back').hidden=simple;
}
document.addEventListener('click',e=>{const button=e.target.closest('[data-mode]');if(button)showMode(button.dataset.mode);});
el('send-code').addEventListener('click',async()=>{
  const button=el('send-code');button.disabled=true;el('message').textContent='';el('message').className='';
  try{
    const response=await fetch('auth/code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:el('email').value})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'验证码未发送');
    if(result.code)el('code').value=result.code;
    el('message').textContent='验证码已发送';
  }catch(error){el('message').textContent=error.message;el('message').className='error';}
  finally{button.disabled=false;}
});
document.addEventListener('click',e=>{
  const button=e.target.closest('[data-provider]');
  if(!button)return;
  el('message').className='';
  el('message').textContent=`${button.dataset.provider}登录即将接入`;
});
el('copy-recovery').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(el('recovery').value);el('message').textContent='已复制';}catch{el('recovery').select();el('message').textContent='请复制恢复密钥';}});
el('auth-form').addEventListener('submit',async e=>{
  e.preventDefault();const button=el('submit');button.disabled=true;el('message').textContent='';
  try{
    if(el('confirm').required&&el('confirm').value!==el('password').value)throw new Error('两次密码不一致');
    const action=mode==='forgot'&&!hosted?'recover':mode;
    const response=await fetch('auth/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:el('email').value,password:el('password').value,key:el('key').value,code:el('code').value,token})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'操作未完成');
    el('message').className='';
    if(mode==='login'){location.assign('workbench');return;}
    if(result.recovery){el('password').value='';el('confirm').value='';el('key').value='';el('code').value='';['email-row','code-row','password-row','confirm-row','key-row'].forEach(id=>el(id).hidden=true);el('recovery').value=result.recovery;el('recovery-row').hidden=false;el('submit').hidden=true;el('message').textContent='请保存恢复密钥，用于找回本机账户。';}
    else if(['verify','reset'].includes(mode)){showMode('login');el('message').textContent='已完成，请登录';}
    else el('message').textContent='若邮箱符合条件，验证邮件将发送至邮箱。';
  }catch(error){el('message').textContent=error.message;el('message').className='error';}finally{button.disabled=false;}
});
async function start(){try{const response=await fetch('auth/config');if(!response.ok)throw new Error('无法连接账户服务');hosted=(await response.json()).hosted;const fragment=new URLSearchParams(location.hash.slice(1));token=fragment.get('reset')||fragment.get('verify')||'';history.replaceState(null,'',location.pathname);showMode(fragment.has('reset')?'reset':fragment.has('verify')?'verify':'login');}catch(error){el('message').textContent=error.message;el('submit').disabled=true;}}
void start();
