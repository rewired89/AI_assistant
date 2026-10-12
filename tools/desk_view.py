"""A face for the desk robot: open http://localhost:8765 in a browser and watch her react (the original artwork below is
drawn for this project, not taken from any franchise). The page shows what the robot's screen will show: the current
animation, power and battery, whether the microphone is muted, and the sentence she is saying. Buttons: Talk (or press
SPACE), Mute (M), Wake, and a battery slider to see low-battery behaviour.

Local only: it listens on 127.0.0.1 and refuses WebSocket connections from any other web page (Origin check), so a
random website you visit cannot press her buttons. It shows state, not recordings, and nothing is stored."""
from __future__ import annotations

import asyncio
import json
import threading
from http import HTTPStatus
from typing import Callable

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

COMMANDS = {"press", "mute", "wake", "battery"}


class ViewServer:
    def __init__(self, snapshot: Callable[[], dict], on_command: Callable[[str, object], None], port: int = 8765,
                 interval_s: float = 0.1):
        self.snapshot, self.on_command, self.port, self.interval = snapshot, on_command, port, interval_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # ---- http page ---------------------------------------------------------------------------
    def _page(self, connection, request):
        if request.path.split("?")[0] == "/ws":
            allowed = {f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"}
            if request.headers.get("Origin") not in allowed:      # only OUR page may drive the robot
                return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
            return None                                          # a WebSocket upgrade: handled below
        if request.path.split("?")[0] != "/":
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        body = PAGE.replace("__PORT__", str(self.port)).encode()
        return Response(200, "OK", Headers([("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                                            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]), body)

    # ---- websocket ----------------------------------------------------------------------------
    async def _handler(self, ws):
        async def sender():
            while True:
                try:
                    await ws.send(json.dumps(self.snapshot()))
                except Exception:
                    return
                await asyncio.sleep(self.interval)
        task = asyncio.ensure_future(sender())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    cmd, arg = msg["cmd"], msg.get("arg")
                except (ValueError, KeyError, TypeError):
                    continue
                if cmd not in COMMANDS:
                    continue
                if cmd == "battery" and not (isinstance(arg, int) and not isinstance(arg, bool) and 0 <= arg <= 100):
                    continue
                self.on_command(cmd, arg)
        finally:
            task.cancel()

    async def _main(self, ready: threading.Event):
        self._stop = asyncio.Event()
        async with serve(self._handler, "127.0.0.1", self.port, process_request=self._page, max_size=4096) as srv:
            self.port = srv.sockets[0].getsockname()[1]
            self.url = f"http://localhost:{self.port}"
            ready.set()
            await self._stop.wait()

    def start(self) -> str:
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._main(ready))

        self._thread = threading.Thread(target=run, name="desk-view", daemon=True)
        self._thread.start()
        ready.wait(5)
        return self.url

    def stop(self) -> None:
        if self._loop and self._stop:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(5)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nine Tails desk robot</title>
<style>
:root{--bg:#f6efe6;--ink:#3b2f2a;--fur:#f2b46d;--fur2:#fbd9a8;--cream:#fff4e3;--tail:#e89a4c;--tip:#fff4e3;--eye:#2b2320;--card:#fffaf2;--line:#e4d6c4;--accent:#c4552d}
@media (prefers-color-scheme:dark){:root{--bg:#1e1a17;--ink:#f1e6d8;--card:#2a2420;--line:#43382f;--cream:#fbe7c8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;padding:16px;gap:12px}
.screen{width:min(92vw,520px);aspect-ratio:1;background:var(--card);border:2px solid var(--line);border-radius:28px;position:relative;overflow:hidden}
svg{width:100%;height:100%}.status{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;font-size:14px}
.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px}
.cap{min-height:3.2em;width:min(92vw,520px);text-align:center;font-size:18px}
.row{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;align-items:center}
button{font:inherit;padding:12px 20px;border-radius:14px;border:2px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.alt{background:transparent;color:var(--accent)}button:focus-visible{outline:3px solid #6aa7ff;outline-offset:2px}
.note{font-size:12px;opacity:.7;max-width:520px;text-align:center}
#face{transform-origin:50% 70%;animation:breathe 4s ease-in-out infinite}@keyframes breathe{50%{transform:scale(1.012)}}
.blink{animation:blink 5s infinite}@keyframes blink{0%,94%,100%{transform:scaleY(1)}97%{transform:scaleY(.08)}}
.eyewrap{transform-box:fill-box;transform-origin:center}
.off{filter:grayscale(1) opacity(.55)}
.ring{opacity:0;transition:opacity .2s}.listening .ring{opacity:1;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.35}}
.zzz{opacity:0}.sleeping .zzz{opacity:1;animation:float 3s infinite}@keyframes float{50%{transform:translateY(-8px)}}
.dots{opacity:0}.thinking .dots{opacity:1}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
</style></head><body>
<div class="screen" id="screen"><svg viewBox="0 0 400 400" role="img" aria-label="Robot face">
<g id="tails"></g>
<g id="facepos"><g id="face">
 <g id="earL"><path d="M105 150 L88 48 L170 105Z" fill="var(--fur)"/><path d="M110 135 L102 75 L152 108Z" fill="var(--cream)"/></g>
 <g id="earR"><path d="M295 150 L312 48 L230 105Z" fill="var(--fur)"/><path d="M290 135 L298 75 L248 108Z" fill="var(--cream)"/></g>
 <ellipse cx="200" cy="215" rx="125" ry="105" fill="var(--fur)"/>
 <path d="M75 235 Q110 300 200 305 Q290 300 325 235 Q300 270 200 275 Q100 270 75 235Z" fill="var(--cream)"/>
 <ellipse cx="200" cy="262" rx="62" ry="40" fill="var(--cream)"/>
 <g id="blush" opacity=".0"><ellipse cx="120" cy="245" rx="20" ry="11" fill="#ff8f8f" opacity=".55"/><ellipse cx="280" cy="245" rx="20" ry="11" fill="#ff8f8f" opacity=".55"/></g>
 <g id="eyes" class="blink eyewrap"><g id="eyeL"></g><g id="eyeR"></g></g>
 <g id="brows" stroke="var(--eye)" stroke-width="7" stroke-linecap="round" fill="none"><path id="browL" d="M120 160 Q148 148 176 160"/><path id="browR" d="M224 160 Q252 148 280 160"/></g>
 <ellipse cx="200" cy="243" rx="13" ry="9" fill="var(--eye)"/>
 <path id="mouth" d="M172 268 Q200 290 228 268" stroke="var(--eye)" stroke-width="6" fill="none" stroke-linecap="round"/>
 <g class="ring"><ellipse cx="200" cy="215" rx="150" ry="130" fill="none" stroke="#6aa7ff" stroke-width="5" stroke-dasharray="10 12"/></g>
 <g class="dots" fill="var(--ink)"><circle cx="300" cy="90" r="7"/><circle cx="325" cy="75" r="9"/><circle cx="355" cy="55" r="11"/></g>
 <text class="zzz" x="305" y="95" font-size="38" fill="var(--ink)">z z</text>
</g></g></svg></div>
<div class="status"><span class="pill" id="s_state">connecting</span><span class="pill" id="s_anim"></span><span class="pill" id="s_pow"></span><span class="pill" id="s_bat"></span><span class="pill" id="s_mic"></span><span class="pill" id="s_stage"></span></div>
<div class="cap" id="cap" aria-live="polite"></div>
<div class="row"><button id="talk">Talk (space)</button><button class="alt" id="mute">Mute mic (m)</button><button class="alt" id="wake">Wake</button>
<label class="pill">battery <input id="bat" type="range" min="0" max="100" value="100"> <span id="batv">100</span>%</label></div>
<div class="note">Test rig only: this is the simulated robot, not the hardware. Space or Talk = the robot's button.</div>
<script>
const $=id=>document.getElementById(id);
const FACES={ // eyes: open|happy|closed|wide|sad|angry|half|up ; mouth: smile|open|flat|o|frown|grin ; brows: n|up|down|sad|angry ; blush
 idle:{e:'open',m:'smile',b:'n'},happy:{e:'happy',m:'grin',b:'up',blush:1},excited:{e:'wide',m:'open',b:'up',blush:1},celebratory:{e:'happy',m:'open',b:'up',blush:1},
 affectionate:{e:'happy',m:'smile',b:'sad',blush:1},calm:{e:'half',m:'smile',b:'n'},sleepy:{e:'half',m:'flat',b:'sad'},sleeping:{e:'closed',m:'flat',b:'n',cls:'sleeping'},
 waking:{e:'half',m:'o',b:'n'},sad:{e:'sad',m:'frown',b:'sad'},angry:{e:'angry',m:'frown',b:'angry'},confused:{e:'open',m:'flat',b:'sad'},curious:{e:'wide',m:'o',b:'up'},
 surprised:{e:'wide',m:'o',b:'up'},listening:{e:'wide',m:'smile',b:'up',cls:'listening'},thinking:{e:'up',m:'flat',b:'n',cls:'thinking'},talking:{e:'open',m:'talk',b:'n'},
 eating:{e:'happy',m:'open',b:'n'},playing:{e:'wide',m:'grin',b:'up'},reflective:{e:'half',m:'smile',b:'sad'},offline:{e:'half',m:'flat',b:'sad',cls:'off'}};
function eye(cx,kind,dir){const c='var(--eye)';
 const open=`<ellipse cx="${cx}" cy="190" rx="19" ry="25" fill="#fff"/><circle cx="${cx+(dir||0)}" cy="${kind==='up'?178:192}" r="14" fill="${c}"/><circle cx="${cx+(dir||0)+5}" cy="${kind==='up'?173:186}" r="4.5" fill="#fff"/>`;
 switch(kind){case 'closed':return `<path d="M${cx-19} 195 Q${cx} 205 ${cx+19} 195" stroke="${c}" stroke-width="6" fill="none" stroke-linecap="round"/>`;
 case 'happy':return `<path d="M${cx-19} 200 Q${cx} 172 ${cx+19} 200" stroke="${c}" stroke-width="7" fill="none" stroke-linecap="round"/>`;
 case 'wide':return `<ellipse cx="${cx}" cy="190" rx="22" ry="29" fill="#fff"/><circle cx="${cx}" cy="192" r="16" fill="${c}"/><circle cx="${cx+6}" cy="185" r="5" fill="#fff"/>`;
 case 'half':return `<ellipse cx="${cx}" cy="196" rx="19" ry="25" fill="#fff"/><circle cx="${cx}" cy="199" r="14" fill="${c}"/><rect x="${cx-24}" y="165" width="48" height="31" fill="var(--fur)"/>`;
 case 'sad':return open+`<path d="M${cx-22} 168 L${cx+22} 178" stroke="var(--fur)" stroke-width="14"/>`;
 case 'angry':return open+`<path d="M${cx-22} 176 L${cx+22} 168" stroke="var(--fur)" stroke-width="14"/>`;
 default:return open;}}
const MOUTH={smile:'M172 268 Q200 290 228 268',grin:'M165 262 Q200 305 235 262 Q200 272 165 262Z',open:'M172 262 Q200 312 228 262Z',o:'M190 268 Q200 292 210 268 Q200 258 190 268Z',flat:'M178 274 L222 274',frown:'M174 284 Q200 262 226 284'};
const BROW={n:['M120 160 Q148 148 176 160','M224 160 Q252 148 280 160'],up:['M120 146 Q148 130 176 146','M224 146 Q252 130 280 146'],down:['M120 168 L176 172','M224 172 L280 168'],
 sad:['M120 168 Q148 160 176 150','M224 150 Q252 160 280 168'],angry:['M120 150 L176 170','M224 170 L280 150']};
const TAILS={EGG:0,BABY:1,YOUNG:3,NINE_TAILS:9,PERSONALIZED_NINE_TAILS:9};
function drawTails(n){let s='';if(n===0){s='<ellipse cx="200" cy="245" rx="120" ry="150" fill="var(--cream)" stroke="var(--line)" stroke-width="4"/><circle cx="110" cy="260" r="9" fill="var(--fur)"/><circle cx="285" cy="215" r="11" fill="var(--fur)"/><circle cx="150" cy="335" r="8" fill="var(--fur)"/><circle cx="270" cy="320" r="7" fill="var(--fur)"/>'}
 else{for(let i=0;i<n;i++){const t=n===1?0:(i/(n-1)-.5)*2;const ang=n===1?58:t*62;s+=`<g transform="translate(200 300) rotate(${ang}) scale(.8) translate(-200 -330)"><path d="M200 330 C150 250 160 120 200 60 C240 120 250 250 200 330Z" fill="var(--tail)" opacity=".95"/><path d="M200 120 C182 90 190 70 200 60 C210 70 218 90 200 120Z" fill="var(--tip)"/></g>`}}
 $('tails').innerHTML=s}
let cur='',talk=0,lastStage='';
function show(anim,speaking){const f=FACES[anim]||FACES.idle;
 $('screen').className='screen '+(f.cls||'');$('s_anim').textContent=anim;
 $('eyeL').innerHTML=eye(150,f.e,f.e==='up'?0:2);$('eyeR').innerHTML=eye(250,f.e,f.e==='up'?0:2);
 const b=BROW[f.b]||BROW.n;$('browL').setAttribute('d',b[0]);$('browR').setAttribute('d',b[1]);$('blush').setAttribute('opacity',f.blush?1:0);
 const speakingNow=speaking||anim==='talking';const mouth=f.m==='talk'||speakingNow?(talk%2?MOUTH.open:MOUTH.smile):MOUTH[f.m];$('mouth').setAttribute('d',mouth);
 $('mouth').setAttribute('fill',(f.m==='open'||f.m==='grin'||f.m==='o'||(speakingNow&&talk%2))?'#8a2f2f':'none');
 $('eyes').classList.toggle('blink',f.e==='open'||f.e==='wide')}
let st={};setInterval(()=>{talk++;if(st.anim)show(st.anim,st.speaking)},150);
const ws=new WebSocket(`ws://localhost:__PORT__/ws`);
ws.onopen=()=>{$('s_state').textContent='connected'};ws.onclose=()=>{$('s_state').textContent='disconnected (restart the desk robot)'};
ws.onmessage=e=>{st=JSON.parse(e.data);
 $('s_state').textContent='she is: '+st.pipe;$('s_pow').textContent=st.power;$('s_bat').textContent='battery '+st.battery+'%';
 $('s_mic').textContent=st.mic?'mic ON':'mic MUTED';$('s_stage').textContent='stage '+st.stage;$('cap').textContent=st.caption||'';
 if(st.stage!==lastStage){lastStage=st.stage;drawTails(TAILS[st.stage]??1);$('facepos').setAttribute('transform',st.stage==='EGG'?'translate(100 130) scale(.5)':'')}
 $('face').classList.toggle('off',st.anim==='offline')};
const send=(cmd,arg)=>ws.readyState===1&&ws.send(JSON.stringify({cmd,arg}));
$('talk').onclick=()=>send('press');$('mute').onclick=()=>send('mute');$('wake').onclick=()=>send('wake');
$('bat').oninput=e=>{$('batv').textContent=e.target.value;send('battery',+e.target.value)};
addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;if(e.code==='Space'){e.preventDefault();send('press')}if(e.key==='m')send('mute')});
drawTails(1);show('idle',false);
</script></body></html>"""
