const el=id=>document.getElementById(id);
let session=JSON.parse(sessionStorage.getItem('agCustomer')||'null'),decision=null;
async function api(path,method='GET',body){
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json',Authorization:'Bearer '+(session?.customer_key||'')},body:body===undefined?undefined:JSON.stringify(body)});
  const result=await r.json();if(!r.ok)throw Error(JSON.stringify(result.detail));return result;
}
function action(id,fn){el(id).onclick=async()=>{el('error').textContent='';el(id).disabled=true;try{await fn()}catch(e){el('error').textContent=e.message}finally{el(id).disabled=false}}}
function show(d){decision=d;el('confirm').hidden=!d?.terms_digest;el('status').textContent=d?.execution||d?.status||'Awaiting evaluation';el('reply').textContent=d?.message||'The sales workflow can now evaluate your request.'}
async function context(){const {booking:b,request:r}=await api('/api/context');el('trip').textContent=b.package;el('fixture').value=b.booking_id;el('booking').replaceChildren();for(const [k,v] of Object.entries({Booking:b.booking_id,Departure:b.departure,Status:b.kind,Upgrade:b.upgraded?'Included':'Standard'})){const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;div.append(dt,dd);el('booking').append(div)}
 if(r){el('message').value=r.message;el('handoffInfo').textContent='Request '+r.id+' · '+b.booking_id+'\nCustomer message: '+r.message+'\nSales-scoped credential: '+session.sales_key+'\nUse the AgentGuard sales skill and get_booking_context. For manual transfer: python integrations/handoff.py evaluate plan.json';}else{el('handoffInfo').textContent='Send an inquiry first.'}}
async function start(fixture){session=await api('/api/sessions','POST',{fixture});sessionStorage.setItem('agCustomer',JSON.stringify(session));el('message').value='';el('handoff').open=false;show(null);await context()}
action('reset',()=>start(el('fixture').value));
action('send',async()=>{await api('/api/requests','POST',{message:el('message').value.trim()});await context();el('handoff').open=true;show(null)});
action('refresh',async()=>{show(await api('/api/latest'));await context()});
action('copy',async()=>{await navigator.clipboard.writeText(el('handoffInfo').textContent)});
action('replay',async()=>{let c=el('case').value;if(c==='discount'&&el('fixture').value!=='B002')await start('B002');if(c!=='discount'&&el('fixture').value==='B002')await start('B001');show(await api('/api/replay/'+c,'POST'));await context()});
action('confirm',async()=>{const r=await api('/api/execute','POST',{decision_id:decision.decision_id,terms_digest:decision.terms_digest,consent:true});el('reply').textContent=r.message;el('status').textContent='Executed in sandbox';el('confirm').hidden=true;await context()});
(async()=>{el('mode').textContent=(await api('/health')).solver;if(!session)await start('B001');else{await context();show(await api('/api/latest'))}})().catch(e=>el('error').textContent=e.message);
