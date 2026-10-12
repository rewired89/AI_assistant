"""The workbench web page (one self-contained HTML string). Served by tools/desk_view.py at /workbench."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nine Tails Sprite Workbench</title>
<style>
:root{--bg:#f6efe6;--ink:#3b2f2a;--card:#fffaf2;--line:#e4d6c4;--accent:#c4552d;--ok:#2f7d4f;--warn:#a86a00;--bad:#b3261e;--mute:#7a6a5d}
:root[data-theme=dark]{--bg:#1e1a17;--ink:#f1e6d8;--card:#2a2420;--line:#43382f;--mute:#b9a996;--ok:#6fcf97;--warn:#f2c94c;--bad:#ff8a80}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:18px;margin:0}h2{font-size:14px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}
.badge{display:inline-block;border-radius:999px;padding:2px 9px;font-size:12px;border:1px solid var(--line);background:var(--card)}
.sim{border-color:var(--warn);color:var(--warn)}.real_frames{border-color:var(--ok);color:var(--ok)}.composed{border-color:#4a6fa5;color:#4a6fa5}
.placeholder{border-color:var(--warn);color:var(--warn)}.missing{border-color:var(--bad);color:var(--bad)}.not_applicable{color:var(--mute)}
main{display:grid;grid-template-columns:minmax(250px,320px) minmax(300px,1fr) minmax(280px,360px);gap:14px;padding:14px;align-items:start}
@media(max-width:1100px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
button,select,input{font:inherit;color:inherit}button{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer}
button:hover{border-color:var(--accent)}button.on{background:var(--accent);color:#fff;border-color:var(--accent)}button:disabled{opacity:.45;cursor:not-allowed}
button.miss{border-style:dashed;color:var(--bad)}button.on.miss{color:#fff;background:var(--bad);border-color:var(--bad)}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}
label{font-size:13px;color:var(--mute)}
#frame{margin:0 auto;background:#111;border-radius:22px;padding:14px;width:max-content;max-width:100%;box-shadow:0 4px 18px #0005}
#frame.round canvas{border-radius:50%}#screen{display:block;image-rendering:pixelated;image-rendering:crisp-edges;background:#000}
#bubble{min-height:46px;margin:8px 0;padding:8px 10px;border-radius:10px;border:1px dashed var(--line)}
#strip{display:flex;gap:6px;overflow-x:auto;padding:4px 0}#strip canvas{image-rendering:pixelated;border:2px solid var(--line);border-radius:6px;cursor:pointer;background:#2b2b3a}
#strip canvas.cur{border-color:var(--accent)}
#insp{image-rendering:pixelated;max-width:100%;border:1px solid var(--line);border-radius:8px;cursor:crosshair}
.cat{margin:8px 0 2px;font-weight:600}.asset{display:grid;grid-template-columns:1fr auto;gap:2px 8px;padding:6px 8px;border:1px solid transparent;border-radius:8px;cursor:pointer}
.asset:hover{border-color:var(--line)}.asset.cur{border-color:var(--accent);background:var(--bg)}.asset small{color:var(--mute);grid-column:1/3}
#log{max-height:240px;overflow:auto;font-size:13px}#log div{padding:3px 0;border-bottom:1px solid var(--line)}
#toast{position:fixed;right:14px;bottom:14px;background:var(--ink);color:var(--bg);padding:8px 12px;border-radius:8px;display:none;max-width:70vw}
.warnbox{border:1px solid var(--warn);border-radius:8px;padding:8px;margin:8px 0;font-size:13px}.err{border-color:var(--bad);color:var(--bad)}
details summary{cursor:pointer}
</style></head><body>
<header><h1>Nine Tails Sprite Workbench</h1>
<span class="badge sim">SIMULATED: nothing here talks to the robot, a language model, or the internet</span>
<span class="badge" id="setLabel"></span><span style="flex:1"></span>
<label>Theme <select id="theme"><option value="light">light</option><option value="dark">dark</option></select></label></header>
<main>
<section id="left"><h2>Sprite sets and assets</h2>
 <div class="row"><label>Set <select id="setSel"></select></label></div>
 <div id="setInfo" class="warnbox" style="display:none"></div>
 <div id="problems"></div><div id="assets"></div>
 <details><summary>How to use this</summary><ol style="padding-left:18px;margin:6px 0">
  <li>Click an asset or a state button: it plays on the robot screen.</li>
  <li>Space = play/pause. Left/Right arrows step one frame.</li>
  <li>Use the simulator on the right to see what the robot would do for an event.</li>
  <li>To change art: replace a PNG in assets/sprites/source/ (see docs/workbench.md). This page reloads by itself.</li></ol></details>
</section>
<section><h2>Robot screen (approximation, not the real display)</h2>
 <div class="row"><label>Screen <select id="scrSel"></select></label>
  <label>W <input id="scrW" type="number" min="32" max="800" style="width:64px"></label><label>H <input id="scrH" type="number" min="32" max="800" style="width:64px"></label>
  <label>Shape <select id="shape"><option>square</option><option>round</option></select></label></div>
 <div class="row"><label>Sprite size on screen <select id="spriteScale"></select></label><label>Window zoom <select id="zoom"></select></label>
  <label>Background <select id="bg"><option value="dark">dark</option><option value="light">light</option><option value="check">checker (shows transparency)</option></select></label></div>
 <div id="frame"><canvas id="screen"></canvas></div>
 <div id="bubble"></div>
 <div class="row"><b id="nowName">-</b><span class="badge" id="nowStatus"></span><span class="badge" id="nowMood">mood: -</span><span id="nowFrame" style="color:var(--mute)"></span></div>
 <div class="row"><button id="play">Pause</button><button id="restart">Restart</button><button id="prev">&larr; Frame</button><button id="next">Frame &rarr;</button>
  <label>Speed <input id="speed" type="range" min="0.1" max="3" step="0.1" value="1" style="width:110px"> <span id="speedV">1.0x</span></label>
  <label><input id="loop" type="checkbox" checked> loop</label></div>
 <div id="strip"></div>
 <h2 style="margin-top:12px">States</h2><div id="states"></div>
</section>
<section><h2>Inspector (zoomed)</h2>
 <canvas id="insp" width="288" height="288"></canvas>
 <div class="row"><label>Zoom <input id="inspZoom" type="range" min="4" max="16" value="6"></label><label><input id="grid" type="checkbox" checked> pixel grid</label></div>
 <div id="pixinfo" style="font-size:13px;color:var(--mute);min-height:20px">Move the mouse over the picture.</div>
 <h2 style="margin-top:12px">Interaction simulator <span class="badge sim">SIMULATED</span></h2>
 <div class="row"><label>Personality <select id="prof"></select></label><label>Mood <select id="mood"></select></label></div>
 <div class="row" id="events"></div>
 <div id="log"><div style="color:var(--mute)">Events you simulate appear here. They use the real behaviour rules but made-up inputs and sample text.</div></div>
</section></main>
<div id="toast"></div>
<script>
"use strict";
const $=id=>document.getElementById(id);
const H={'X-NT-Edit':'1','Content-Type':'application/json'};
let cat=null,setName=null,spriteDoc=null,cache={},cur=null,frame=0,acc=0,last=performance.now(),playing=true,fp=null,seqTimer=null;
const EVENTS=[['message','Receive a message'],['speak','Speak'],['listen','Listen (mic)'],['think','Think'],['wake','Wake up'],['wake_scheduled','Scheduled wake'],['sleep','Go to sleep'],
 ['battery_warning','Low-battery warning'],['battery_low','Low battery'],['touch','Touch'],['idle','Idle tick'],['offline','Host offline'],['reset','Reset simulator']];
function pref(k,d){try{const v=localStorage.getItem('ntwb_'+k);return v===null?d:v}catch(e){return d}}
function setPref(k,v){try{localStorage.setItem('ntwb_'+k,v)}catch(e){}}
function toast(t,ms=3500){const e=$('toast');e.textContent=t;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',ms)}
async function jget(u){const r=await fetch(u);if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||r.status);return r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:H,body:JSON.stringify(b||{})});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||r.status);return j}
function hex(c){const m=/^#(..)(..)(..)(..)?$/.exec(c);return[parseInt(m[1],16),parseInt(m[2],16),parseInt(m[3],16),m[4]?parseInt(m[4],16):255]}
const ALPHA="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+-";
function frameCanvas(f){const w=spriteDoc.width,h=spriteDoc.height,c=document.createElement('canvas');c.width=w;c.height=h;const g=c.getContext('2d'),img=g.createImageData(w,h);
 const pal=spriteDoc.palette.map(hex);for(let y=0;y<h;y++)for(let x=0;x<w;x++){const p=pal[ALPHA.indexOf(f.rows[y][x])],i=(y*w+x)*4;img.data[i]=p[0];img.data[i+1]=p[1];img.data[i+2]=p[2];img.data[i+3]=p[3]}g.putImageData(img,0,0);return c}
function frames(id){if(!spriteDoc||!spriteDoc.animations[id])return null;const key=setName+':'+id;if(!cache[key])cache[key]=spriteDoc.animations[id].frames.map(f=>({ms:f.ms,c:frameCanvas(f),rows:f.rows}));return cache[key]}
function row(id){return cat.sets.find(s=>s.name===setName).animations.find(a=>a.id===id)}
function bgFill(g,w,h){const m=$('bg').value;if(m==='check'){const s=8;for(let y=0;y<h;y+=s)for(let x=0;x<w;x+=s){g.fillStyle=((x/s+y/s)%2)?'#3a3a4a':'#2b2b3a';g.fillRect(x,y,s,s)}}else{g.fillStyle=m==='light'?'#f4f1ea':'#15121a';g.fillRect(0,0,w,h)}}
function layout(){const W=+$('scrW').value,H2=+$('scrH').value,z=+$('zoom').value,c=$('screen');c.width=W;c.height=H2;c.style.width=W*z+'px';c.style.height=H2*z+'px';$('frame').className=$('shape').value==='round'?'round':''}
function draw(){const c=$('screen'),g=c.getContext('2d');g.imageSmoothingEnabled=false;bgFill(g,c.width,c.height);const a=cur&&row(cur),fr=cur&&frames(cur);
 if(!fr){g.fillStyle='#b3261e55';for(let i=-c.height;i<c.width;i+=14){g.beginPath();g.moveTo(i,c.height);g.lineTo(i+c.height,0);g.lineTo(i+c.height+6,0);g.lineTo(i+6,c.height);g.fill()}
  g.fillStyle='#fff';g.font='bold '+Math.max(10,c.width/16)+'px sans-serif';g.textAlign='center';g.fillText(a&&a.status==='not_applicable'?'NOT APPLICABLE':'MISSING ART',c.width/2,c.height/2);
  g.font=Math.max(9,c.width/22)+'px sans-serif';g.fillText(cur||'',c.width/2,c.height/2+c.width/12);drawInsp(null);return}
 const f=fr[Math.min(frame,fr.length-1)];let k=+$('spriteScale').value;if(!k)k=Math.max(1,Math.floor(Math.min(c.width/spriteDoc.width,c.height/spriteDoc.height)));
 g.drawImage(f.c,Math.round((c.width-spriteDoc.width*k)/2),Math.round((c.height-spriteDoc.height*k)/2),spriteDoc.width*k,spriteDoc.height*k);drawInsp(f)}
function drawInsp(f){const c=$('insp'),z=+$('inspZoom').value,w=spriteDoc?spriteDoc.width:48,h=spriteDoc?spriteDoc.height:48;c.width=w*z;c.height=h*z;const g=c.getContext('2d');g.imageSmoothingEnabled=false;
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){g.fillStyle=((x+y)%2)?'#34343f':'#2b2b35';g.fillRect(x*z,y*z,z,z)}
 if(f)g.drawImage(f.c,0,0,w*z,h*z);if($('grid').checked&&z>=6){g.strokeStyle='#ffffff22';g.beginPath();for(let x=0;x<=w;x++){g.moveTo(x*z+.5,0);g.lineTo(x*z+.5,h*z)}for(let y=0;y<=h;y++){g.moveTo(0,y*z+.5);g.lineTo(w*z,y*z+.5)}g.stroke()}}
function status(){const a=cur&&row(cur);$('nowName').textContent=a?a.label+' ('+cur+')':'-';const s=$('nowStatus');s.textContent=a?a.status.replace('_',' '):'';s.className='badge '+(a?a.status:'');
 const fr=cur&&frames(cur);$('nowFrame').textContent=fr?`frame ${Math.min(frame,fr.length-1)+1}/${fr.length}, ${fr[Math.min(frame,fr.length-1)].ms} ms, ${a.loop?'loops':'plays once'}`:(a?a.note||'no frames':'');
 const st=$('strip');st.querySelectorAll('canvas').forEach((c,i)=>c.className=i===frame?'cur':'')}
function buildStrip(){const st=$('strip');st.innerHTML='';const fr=cur&&frames(cur);if(!fr)return;fr.forEach((f,i)=>{const c=document.createElement('canvas');c.width=spriteDoc.width;c.height=spriteDoc.height;c.style.width=Math.min(72,spriteDoc.width*2)+'px';c.style.height=Math.min(72,spriteDoc.height*2)+'px';c.title='frame '+(i+1)+', '+f.ms+' ms';c.getContext('2d').drawImage(f.c,0,0);c.onclick=()=>{playing=false;$('play').textContent='Play';frame=i;acc=0;status();draw()};st.appendChild(c)})}
function choose(id){clearTimeout(seqTimer);cur=id;frame=0;acc=0;buildStrip();status();draw();document.querySelectorAll('.asset').forEach(e=>e.classList.toggle('cur',e.dataset.id===id));document.querySelectorAll('#states button').forEach(b=>b.classList.toggle('on',b.dataset.id===id))}
function tick(now){const dt=now-last;last=now;const fr=cur&&frames(cur);if(fr&&playing&&fr.length>0){acc+=dt*+$('speed').value;let ms=fr[frame].ms;while(acc>=ms){acc-=ms;if(frame+1<fr.length)frame++;else if($('loop').checked&&row(cur).loop!==false)frame=0;else{acc=0;break}ms=fr[frame].ms}status();draw()}requestAnimationFrame(tick)}
function buildLists(){const s=cat.sets.find(x=>x.name===setName);$('setLabel').textContent=s.label+(s.width?` — ${s.width}x${s.height}, ${s.colors} colours`:'');
 const info=$('setInfo');info.style.display=s.error?'block':'none';info.className='warnbox'+(s.error?' err':'');info.textContent=s.error?'This sprite file is broken: '+s.error:'';
 $('problems').innerHTML=cat.problems.length?'<div class="warnbox err"><b>Asset checks found problems:</b><br>'+cat.problems.map(p=>p.replace(/</g,'&lt;')).join('<br>')+'</div>':'<div class="warnbox" style="border-color:var(--ok);color:var(--ok)">Asset checks: all clean.</div>';
 const A=$('assets');A.innerHTML='';const S=$('states');S.innerHTML='';
 cat.categories.forEach(c=>{const items=s.animations.filter(a=>a.category===c);if(!items.length)return;
  const h=document.createElement('div');h.className='cat';h.textContent=c;A.appendChild(h);const h2=h.cloneNode(true);S.appendChild(h2);const wrap=document.createElement('div');wrap.className='row';S.appendChild(wrap);
  items.forEach(a=>{const d=document.createElement('div');d.className='asset';d.dataset.id=a.id;d.onclick=()=>choose(a.id);
   const dims=a.width?`${a.width}x${a.height}`:'-';const speed=a.frames?`${a.avg_ms} ms/frame`:'-';
   d.innerHTML=`<span>${a.label}</span><span class="badge ${a.status}">${a.status.replace('_',' ')}</span><small>${a.id} | ${dims} | ${a.frames} frame${a.frames==1?'':'s'} | ${speed}${a.note?' | '+a.note.replace(/</g,'&lt;'):''}</small>`;A.appendChild(d);
   const b=document.createElement('button');b.dataset.id=a.id;b.textContent=a.label.split(' (')[0];if(a.status==='missing'||a.status==='not_applicable'){b.className='miss';b.title=a.status.replace('_',' ')}b.onclick=()=>choose(a.id);wrap.appendChild(b)})})}
async function loadAll(keep){cat=await jget('/api/catalog');cache={};if(!setName||!cat.sets.find(s=>s.name===setName))setName=pref('set','default');if(!cat.sets.find(s=>s.name===setName))setName=cat.sets[0].name;
 const sel=$('setSel');sel.innerHTML=cat.sets.map(s=>`<option value="${s.name}">${s.label}</option>`).join('');sel.value=setName;
 try{spriteDoc=await jget('/api/sprites?set='+encodeURIComponent(setName))}catch(e){spriteDoc=null;toast('Could not load '+setName+': '+e.message)}
 if(!spriteDoc){$('assets').textContent='No sprite data.';return}
 const pc=$('prof');if(!pc.options.length){pc.innerHTML=cat.profiles.map(p=>`<option>${p}</option>`).join('');if(cat.profiles.includes('ninetales'))pc.value='ninetales';$('mood').innerHTML=cat.moods.map(p=>`<option>${p}</option>`).join('')}
 buildLists();const want=keep&&cur&&row(cur)?cur:(row('idle')?'idle':cat.sets.find(s=>s.name===setName).animations[0].id);choose(want)}
function pickScale(){const sel=$('spriteScale');sel.innerHTML='<option value="0">auto (largest that fits)</option>'+[1,2,3,4,5,6,8].map(k=>`<option value="${k}">${k}x</option>`).join('');sel.value=pref('ss','0');
 $('zoom').innerHTML=[1,2,3,4].map(k=>`<option value="${k}">${k}x</option>`).join('');$('zoom').value=pref('zoom','2')}
function buildScreens(){const sel=$('scrSel');sel.innerHTML=cat.screens.map(s=>`<option value="${s.id}">${s.label}</option>`).join('')+'<option value="custom">custom</option>';
 const saved=pref('scr',cat.screens[0].id);sel.value=cat.screens.find(s=>s.id===saved)||saved==='custom'?saved:cat.screens[0].id;applyScreen(false)}
function applyScreen(fromSel){const sel=$('scrSel');const s=cat.screens.find(x=>x.id===sel.value);if(s){$('scrW').value=s.width;$('scrH').value=s.height;$('shape').value=s.shape}setPref('scr',sel.value);layout();draw()}
function logEv(r){const d=document.createElement('div');const steps=r.steps.map(s=>`<b>${s.animation}</b>${s.dialogue?': “'+s.dialogue.replace(/</g,'&lt;')+'”':''}${s.voice_style?' <i>('+s.voice_style+' voice)</i>':''}`).join(' &rarr; ');
 d.innerHTML=`<span class="badge sim">SIMULATED</span> <b>${r.event}</b> [${r.species}, mood ${r.mood}] ${steps||'<i>nothing happens</i>'}${r.notes.length?'<br><small>'+r.notes.map(n=>n.replace(/</g,'&lt;')).join(' ')+'</small>':''}`;const L=$('log');L.prepend(d)}
function playSteps(steps,i){clearTimeout(seqTimer);if(i>=steps.length)return;const s=steps[i];if(row(s.animation)){choose(s.animation)}else{choose(s.animation)}
 $('bubble').innerHTML=`<span class="badge sim">SIMULATED</span> `+(s.dialogue?'“'+s.dialogue.replace(/</g,'&lt;')+'” ':'')+`<small style="color:var(--mute)">${s.label}${s.voice_style?' | voice: '+s.voice_style:''}</small>`;
 seqTimer=setTimeout(()=>playSteps(steps,i+1),s.hold_ms)}
async function sim(ev){try{const r=await jpost('/api/simulate',{event:ev,species:$('prof').value,mood:$('mood').value});logEv(r);$('nowMood').textContent='mood: '+r.mood;if(r.steps.length)playSteps(r.steps,0);else $('bubble').innerHTML='<span class="badge sim">SIMULATED</span> <small>'+(r.notes[0]||'no change')+'</small>'}catch(e){toast('Simulator: '+e.message)}}
async function poll(){try{const r=await jpost('/api/refresh',{});if(r.errors.length)toast('Could not rebuild: '+r.errors.join('; '),7000);if(fp!==null&&r.fingerprint!==fp){toast(r.rebuilt.length?r.rebuilt.join('; '):'Files changed: reloaded');await loadAll(true)}fp=r.fingerprint}catch(e){}setTimeout(poll,1500)}
function init(){const t=pref('theme','light');$('theme').value=t;document.documentElement.dataset.theme=t;$('theme').onchange=e=>{document.documentElement.dataset.theme=e.target.value;setPref('theme',e.target.value)};
 $('bg').value=pref('bg','dark');$('bg').onchange=e=>{setPref('bg',e.target.value);draw()};pickScale();
 $('spriteScale').onchange=e=>{setPref('ss',e.target.value);draw()};$('zoom').onchange=e=>{setPref('zoom',e.target.value);layout();draw()};
 $('scrSel').onchange=()=>applyScreen(true);['scrW','scrH','shape'].forEach(i=>$(i).onchange=()=>{$('scrSel').value='custom';setPref('scr','custom');layout();draw()});
 $('setSel').onchange=async e=>{setName=e.target.value;setPref('set',setName);await loadAll(true)};
 $('play').onclick=()=>{playing=!playing;$('play').textContent=playing?'Pause':'Play'};$('restart').onclick=()=>{frame=0;acc=0;playing=true;$('play').textContent='Pause';status();draw()};
 const step=d=>{const fr=cur&&frames(cur);if(!fr)return;playing=false;$('play').textContent='Play';frame=(frame+d+fr.length)%fr.length;acc=0;status();draw()};$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);
 $('speed').oninput=e=>$('speedV').textContent=(+e.target.value).toFixed(1)+'x';$('inspZoom').oninput=draw;$('grid').onchange=draw;
 document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key===' '){e.preventDefault();$('play').click()}if(e.key==='ArrowRight')step(1);if(e.key==='ArrowLeft')step(-1)});
 $('insp').onmousemove=e=>{const c=$('insp'),r=c.getBoundingClientRect(),z=+$('inspZoom').value,k=c.width/r.width,x=Math.floor((e.clientX-r.left)*k/z),y=Math.floor((e.clientY-r.top)*k/z);const fr=cur&&frames(cur);
  if(!fr||x<0||y<0||x>=spriteDoc.width||y>=spriteDoc.height)return;const f=fr[Math.min(frame,fr.length-1)],ch=f.rows[y][x],i=ALPHA.indexOf(ch);$('pixinfo').textContent=`x ${x}, y ${y}: palette ${i} = ${spriteDoc.palette[i]}${i===0?' (transparent)':''}`};
 $('events').innerHTML=EVENTS.map(([k,l])=>`<button data-ev="${k}">${l}</button>`).join('');document.querySelectorAll('#events button').forEach(b=>b.onclick=()=>sim(b.dataset.ev));
 loadAll(false).then(()=>{buildScreens();draw();poll()}).catch(e=>toast('Could not start: '+e.message,9000));requestAnimationFrame(tick)}
init();
</script></body></html>
"""
