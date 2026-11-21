'use strict';

const byId = (id) => document.getElementById(id);
const input = byId('message');
const wallet = byId('wallet-details');
const smallScreen = window.matchMedia('(max-width:800px)');
const currency = new Intl.NumberFormat('en-SG', {style:'currency', currency:'SGD', currencyDisplay:'code'});
const dateFormat = new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
let state = null;
let sending = false;
let timer = null;
let lastMessage = 0;
let pending = null;
const pendingKey = 'northline-pending-v2';

function displayError(message='') {
  byId('error').textContent = message;
  byId('error').hidden = !message;
}
async function request(url, body, method = body === undefined ? 'GET' : 'POST') {
  const options = {method, credentials:'same-origin', cache:'no-store'};
  if (body !== undefined) {
    options.headers = {'Content-Type':'application/json'};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : 'Please check your message and try again.');
    error.status = response.status;
    throw error;
  }
  return value;
}
function field(id, value) {
  const node = byId(id);
  node.textContent = value || '—';
  node.classList.toggle('unknown', !value);
}
function formatDate(value) {
  if (!value) return '';
  return dateFormat.format(new Date(value+'T12:00:00Z'));
}
function showTrip(t) {
  const labels = {draft:'Planning',review:'Ready to book',booked:'Confirmed'};
  byId('phase').textContent = t ? labels[t.phase] : 'Not booked';
  byId('phase').dataset.phase = t?.phase || '';
  byId('trip-name').textContent = t?.destination || t?.package || 'Your next trip';
  byId('booking-ref').textContent = t ? (t.phase==='booked'?'Booking ':'Itinerary ')+t.id : 'Details appear as you plan.';
  field('origin',t?.origin); field('destination',t?.destination);
  field('departure',formatDate(t?.departure)); field('return-date',formatDate(t?.return_date));
  field('travellers',t?.travellers ? String(t.travellers) : null); field('lead',t?.lead_name);
  field('cabin',t?.cabin?.replaceAll('_',' '));
  field('seat-preference', t?.seat_preference);
  showFlightDetails(t);
  const photo=t?.photo;
  byId('destination-cover').hidden=!photo;
  if(photo) {
    byId('destination-photo').src=photo.url;
    byId('destination-photo').alt=photo.title+' destination photograph';
    byId('photo-credit').textContent=photo.credit || photo.title+' · Photo source & licence';
    byId('photo-credit').href=photo.credit_url || photo.source_url;
  } else byId('destination-photo').removeAttribute('src');
  const select=byId('booking-select');
  select.replaceChildren(...(state?.trips || []).map(trip=>{
    const option=document.createElement('option');option.value=trip.id;
    option.textContent=trip.destination+' · '+trip.id;option.selected=trip.id===t?.id;return option;
  }));
  byId('delete-booking').disabled=!t || sending || t.reviewing;
  field('price', t?.price_cents != null ? currency.format(t.price_cents/100) : null);
  byId('price-label').textContent = t?.fare_demo ? 'Simulated demo total' : t?.fare_estimate ? 'Estimated trip total' : t?.phase==='review' ? (t.fare_live?'Airline quote':'Test airline quote') : 'Trip total';
  const fareLink = byId('fare-source');
  fareLink.hidden = !t?.fare_url && !t?.fare_search_url;
  if (!fareLink.hidden) {
    fareLink.href = t.fare_url || t.fare_search_url;
    fareLink.textContent = t.fare_url ? 'View advertised fare source' : 'Compare current fares';
  }
  const lines=[];
  if (t?.airline_preference) lines.push('Airline preference · '+t.airline_preference);
  if (t?.upgraded) lines.push('Room upgraded');
  if (t?.credit_cents) lines.push(currency.format(t.credit_cents/100)+' credit applied');
  if (t?.queued_requests) lines.push(t.queued_requests+' saved '+(t.queued_requests===1?'request':'requests'));
  if (t?.reviewing) lines.push('Negotiation in review');
  byId('extras').textContent = lines.join(' · '); byId('extras').hidden = !lines.length;
}
function sourceLink(label, url) {
  const link = document.createElement('a');
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = label;
  return link;
}
function showFlightDetails(trip) {
  const panel = byId('flight-details');
  panel.hidden = !trip?.origin || !trip?.destination;
  const carriers = byId('operating-airlines');
  carriers.hidden = !trip?.airlines?.length;
  carriers.textContent = carriers.hidden ? '' :
    (trip.outbound_airlines_only ? 'Outbound airlines in fare search' : trip.fare_estimate ? 'Advertised airline' : trip.fare_live ? 'Operating airline' : 'Airline in test data') + ' · ' + trip.airlines.join(', ');

  const routes = byId('route-airlines');
  routes.replaceChildren(...(trip?.route_airlines || []).map(route => {
    const row = document.createElement('li');
    row.append(sourceLink(route.name, route.source_url), ' · ' + route.service);
    return row;
  }));
  routes.hidden = !routes.children.length;

  const seats = byId('seat-maps');
  seats.replaceChildren(...(trip?.seat_maps || []).map(map => {
    const row = document.createElement('li');
    const options = map.available.join(', ') || 'No matching seats shown';
    row.textContent = map.flight + ' ' + map.from + '–' + map.to + ' · ' + options;
    return row;
  }));
  seats.hidden = !seats.children.length;
  const status = byId('seat-status');
  status.hidden = panel.hidden;
  if (trip?.flight_details_stale) {
    status.textContent = 'The date changed. The airline and seats for the new dates need a fresh check.';
  } else if (trip?.phase === 'booked') {
    status.textContent = 'Seats are unassigned.';
  } else if (!seats.hidden) {
    status.textContent = (trip.fare_live ? 'Seat-map availability snapshot' : 'Amadeus test seat map') + ' · Seats are unassigned.';
  } else {
    status.textContent = 'Seat availability is unverified. You can save a preference.';
  }
}
function setHints(t) {
  let hints=[];
  if (state?.messages.length) {
    if (!t) hints=['Plan a trip to Bali','List my trips'];
    else if (t.phase==='review') hints=['Confirm booking','Explain my quote'];
    else if (t.phase==='booked' && t.has_offer) hints=['Accept offer','Decline offer','Explain this decision'];
    else if (t.phase==='booked' && !t.reviewing) hints=t.has_decision ? ['Explain this decision','Show the assumptions','Book another trip'] : ['Ask for SGD 10 off','Move my trip 14 days later','Book another trip'];
    else if (t.phase==='draft') hints=['Show my itinerary','How do bookings work?'];
  }
  const buttons=hints.map(label=>{
    const button=document.createElement('button');
    button.type='button'; button.dataset.prompt=label; button.textContent=label;
    return button;
  });
  byId('hints').replaceChildren(...buttons);
}
function addMessage(m) {
  const item=document.createElement('article');
  item.className='msg '+m.role;
  const label=document.createElement('span'); label.className='msg-label';
  label.textContent=m.role==='user'?'You':'Northline';
  const bubble=document.createElement('div'); bubble.className='bubble'; bubble.textContent=m.body;
  item.append(label, bubble);
  if (m.photo) {
    const figure = document.createElement('figure');
    figure.className = 'confirmation-photo';
    const photo = document.createElement('img');
    photo.src = m.photo.url;
    photo.alt = m.photo.title;
    const caption = document.createElement('figcaption');
    caption.append(sourceLink(m.photo.credit || m.photo.title + ' · Photo source & licence', m.photo.credit_url || m.photo.source_url));
    figure.append(photo, caption);
    item.append(figure);
  }
  byId('messages').append(item);
}
function controls() {
  const disabled=sending || !state;
  byId('send').disabled=disabled;
  input.readOnly=sending;
  for(const btn of document.querySelectorAll('[data-prompt]')) btn.disabled=disabled;
  byId('activity').hidden=!(sending || state?.working);
  byId('activity').textContent=sending?'Northline is reading your message…':state?.working?'The negotiation agent is checking your request…':'';
}
function render(next) {
  if(state && next.revision < state.revision) return;
  const pane=byId('conversation');
  const nearBottom=pane.scrollHeight-pane.scrollTop-pane.clientHeight<90;
  if(state?.itinerary?.id!==next.itinerary?.id || next.messages.length < byId('messages').children.length) {
    byId('messages').replaceChildren();lastMessage=0;
  }
  state=next;
  byId('welcome').hidden=next.messages.length>0;
  for(const m of next.messages) {
    if(m.seq>lastMessage) {addMessage(m); lastMessage=m.seq;}
  }
  showTrip(next.itinerary); setHints(next.itinerary); controls();
  if (sending || nearBottom) pane.scrollTop=pane.scrollHeight;
  clearTimeout(timer);
  if(next.working) timer=setTimeout(refresh,850);
  else if(next.offer_expires_at) timer=setTimeout(refresh,Math.max(250,next.offer_expires_at*1000-Date.now()+200));
}
async function refresh() {
  try {render(await request('/api/chat'));displayError();}
  catch(e) {
    if (e.status === 401) {
      window.location.reload();
      return;
    }
    displayError('Connection interrupted. Reconnecting to your itinerary…');
    timer=setTimeout(refresh,2200);
  }
}
function resize() {
  input.style.height='auto';input.style.height=Math.min(110,input.scrollHeight)+'px';
}
function remember(p) {
  pending=p;
  try {if(p) sessionStorage.setItem(pendingKey,JSON.stringify(p)); else sessionStorage.removeItem(pendingKey);} catch {}
}
async function deliver(text) {
  if(!state || sending || !text.trim()) return;
  sending=true;displayError();controls();
  const body=(pending && pending.message===text.trim()) ? pending : {
    nonce:crypto.randomUUID(),revision:state.revision,message:text.trim()
  };
  remember(body);
  try {
    render(await request('/api/chat/messages',body));
    remember(null);input.value='';resize();
    byId('conversation').scrollTop=byId('conversation').scrollHeight;
  } catch(e) {
    if(e.status) remember(null);
    input.value=text;resize();
    displayError(e.status?e.message:'Couldn’t reach the travel desk. Send again to retry this message.');
    if(e.status===401 || e.status===409) await refresh();
  } finally {sending=false;controls();input.focus({preventScroll:true});}
}
byId('composer').addEventListener('submit',(e)=>{e.preventDefault();deliver(input.value);});
byId('destination-photo').addEventListener('error',()=>{byId('destination-cover').hidden=true;});
byId('booking-select').addEventListener('change',async e=>{
  if(sending)return;
  sending=true;controls();
  try {render(await request('/api/chat/trips/'+encodeURIComponent(e.target.value)+'/select',{}));}
  catch(error){displayError(error.message);}finally{sending=false;controls();}
});
byId('new-booking').addEventListener('click',()=>deliver('Book another trip'));
byId('delete-booking').addEventListener('click',async()=>{
  const trip=state?.itinerary;
  if(!trip || sending || !confirm('Permanently delete '+trip.id+' and its booking, messages and decision records?'))return;
  sending=true;controls();
  try {remember(null);render(await request('/api/chat/trips/'+encodeURIComponent(trip.id),undefined,'DELETE'));}
  catch(error){displayError(error.message);}finally{sending=false;controls();}
});
input.addEventListener('input',resize);
input.addEventListener('keydown',e=>{
  if(e.key==='Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault();deliver(input.value);}
});
document.addEventListener('click',e=>{
  const button=e.target.closest('button[data-prompt]');
  if(button) deliver(button.dataset.prompt);
});
function adaptWallet() {
  wallet.open=!smallScreen.matches;
  wallet.querySelector('summary').tabIndex=smallScreen.matches?0:-1;
}
smallScreen.addEventListener('change',adaptWallet);adaptWallet();
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && state && !sending) refresh();});
(async()=>{
  controls();
  try {
    let current=await request('/api/chat/session',{});
    if (current.new_session) remember(null);
    try {
      const prev=JSON.parse(sessionStorage.getItem('agCustomer') || 'null');
      if(prev?.customer_key && !current.messages.length && !current.itinerary) {
        current=await request('/api/chat/import',{customer_key:prev.customer_key});
      }
    } catch (error) {
      if (error.status === 403) sessionStorage.removeItem('agCustomer');
    }
    render(current);
    try {pending=JSON.parse(sessionStorage.getItem(pendingKey)||'null');} catch {}
    if(pending?.message) await deliver(pending.message);
  } catch {displayError('The travel desk could not connect. Check that the local server is running, then reload.');}
})();
