"""The desk robot's window: her face (what the robot's screen will show), a live mic meter, and a PIXEL SPRITE EDITOR to
change that screen's art. Open the printed address in a browser.

    http://localhost:8765/          her face reacting live (sprites if assets/sprites/default.json exists, else a vector face)
    http://localhost:8765/editor    draw and edit the sprites, preview at the screen size, save into assets/sprites/

Run only the editor (no robot, no microphone):   python -m tools.desk_view

Local only: it listens on 127.0.0.1, checks the Host header (DNS-rebinding), and every state-changing request must carry a
custom header and a matching Origin, so other web pages cannot press her buttons or overwrite the art. It shows state, not
recordings. The sprite file is plain JSON in the repo (assets/sprites/), so changes are normal git changes you review and
commit. The art is original; nothing is taken from any franchise. Screen size and shape are UNVERIFIED until the real board
is known: the editor lets you set them and the preview follows."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from companion import sprites as sp
from companion import sprites_default

COMMANDS = {"press", "mute", "wake", "battery"}
MAX_BODY = sp.MAX_BYTES + 4096
DEFAULT_SPRITES = Path(__file__).resolve().parent.parent / "assets" / "sprites" / "default.json"


class ViewServer:
    def __init__(self, snapshot: Callable[[], dict], on_command: Callable[[str, object], None], port: int = 8765,
                 sprites_path: Path | str = DEFAULT_SPRITES):
        self.snapshot, self.on_command, self.port = snapshot, on_command, port
        self.sprites_path = Path(sprites_path)
        self.rev = 0                                           # bumped on every sprite save so open pages reload them
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # ---- sprite files -------------------------------------------------------------------------
    STAGE_FILES = {"EGG": "egg.json", "BABY": "baby.json"}    # other stages use the main set until their art exists

    def sprite_doc(self, stage: str | None = None) -> dict:
        try:
            name = self.STAGE_FILES.get(stage or "")
            if name and (self.sprites_path.parent / name).is_file():
                return sp.load(self.sprites_path.parent / name)
            return sp.load(self.sprites_path)
        except sp.SpriteError:
            return sprites_default.make()                      # nothing saved yet (or the file is damaged): the starter set

    # ---- http ---------------------------------------------------------------------------------
    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "NineTailsDesk"
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            # -- helpers
            def _allowed_hosts(self) -> set[str]:
                return {f"localhost:{server.port}", f"127.0.0.1:{server.port}"}

            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj) -> None:
                self._send(status, json.dumps(obj).encode())

            def _host_ok(self) -> bool:
                if self.headers.get("Host") in self._allowed_hosts():
                    return True
                self._json(403, {"error": "bad host"})
                return False

            def _write_ok(self) -> bool:
                origin = self.headers.get("Origin")
                if self.headers.get("X-NT-Edit") != "1" or (origin is not None and origin not in
                                                            {f"http://{h}" for h in self._allowed_hosts()}):
                    self._json(403, {"error": "forbidden"})
                    return False
                return True

            def _body(self) -> bytes | None:
                try:
                    n = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    n = -1
                if not 0 <= n <= MAX_BODY:
                    self._json(413 if n > MAX_BODY else 400, {"error": "bad length"})
                    return None
                return self.rfile.read(n)

            # -- routes
            def do_GET(self):
                if not self._host_ok():
                    return
                path = self.path.split("?")[0]
                if path == "/":
                    return self._send(200, VIEWER.encode(), "text/html")
                if path == "/editor":
                    return self._send(200, EDITOR.encode(), "text/html")
                if path == "/api/state":
                    try:
                        return self._json(200, {**server.snapshot(), "rev": server.rev})
                    except Exception:
                        return self._json(500, {"error": "state unavailable"})
                if path == "/api/sprites":
                    q = parse_qs(urlsplit(self.path).query).get("stage", [""])[0].upper()[:24]
                    return self._json(200, server.sprite_doc(q or None))
                if path == "/favicon.ico":
                    return self._send(204, b"", "image/x-icon")
                if path == "/api/sprites/default":
                    return self._json(200, sprites_default.make())
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if not (self._host_ok() and self._write_ok()):
                    return
                if self.path != "/api/cmd":
                    return self._json(404, {"error": "not found"})
                raw = self._body()
                if raw is None:
                    return
                try:
                    msg = json.loads(raw)
                    cmd, arg = msg["cmd"], msg.get("arg")
                except (ValueError, KeyError, TypeError):
                    return self._json(400, {"error": "bad command"})
                if cmd not in COMMANDS or (cmd == "battery" and not (isinstance(arg, int) and not isinstance(arg, bool) and 0 <= arg <= 100)):
                    return self._json(400, {"error": "bad command"})
                server.on_command(cmd, arg)
                self._json(200, {"ok": True})

            def do_PUT(self):
                if not (self._host_ok() and self._write_ok()):
                    return
                if self.path != "/api/sprites":
                    return self._json(404, {"error": "not found"})
                raw = self._body()
                if raw is None:
                    return
                try:
                    sp.save(server.sprites_path, json.loads(raw))
                except (sp.SpriteError, ValueError) as e:
                    return self._json(400, {"error": str(e)[:300]})
                except OSError:
                    return self._json(500, {"error": "could not write the sprite file"})
                server.rev += 1
                self._json(200, {"ok": True, "rev": server.rev})

        return Handler

    def start(self) -> str:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), self._make_handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.url = f"http://localhost:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="desk-view", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(5)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time
    ap = argparse.ArgumentParser(description="Open only the sprite editor (no robot).")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--sprites", default=str(DEFAULT_SPRITES))
    a = ap.parse_args(argv)
    state = {"anim": "idle", "power": "AWAKE", "battery": 100, "mic": True, "pipe": "idle", "stage": "NINE_TAILS", "caption": "",
             "speaking": False, "notes": [], "level": 0, "threshold": 0, "heard": ""}
    v = ViewServer(lambda: state, lambda c, x: None, port=a.port, sprites_path=a.sprites)
    print(f"Sprite editor: {v.start()}/editor   (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        v.stop()
    return 0


# =====================================================================================================================
_COMMON_CSS = r"""
:root{--bg:#f6efe6;--ink:#3b2f2a;--card:#fffaf2;--line:#e4d6c4;--accent:#c4552d;--fur:#f2b46d;--cream:#fff4e3;--tail:#e89a4c;--tip:#fff4e3;--eye:#2b2320}
@media (prefers-color-scheme:dark){:root{--bg:#1e1a17;--ink:#f1e6d8;--card:#2a2420;--line:#43382f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,sans-serif;padding:16px}
button,select,input{font:inherit}button{padding:8px 14px;border-radius:12px;border:2px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.alt{background:transparent;color:var(--accent)}button:focus-visible,select:focus-visible,input:focus-visible,canvas:focus-visible{outline:3px solid #6aa7ff;outline-offset:2px}
button[aria-pressed=true]{outline:3px solid var(--ink)}.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:14px}
.note{font-size:12px;opacity:.7}a{color:var(--accent)}
"""

VIEWER = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nine Tails desk robot</title><style>""" + _COMMON_CSS + r"""
body{display:flex;flex-direction:column;align-items:center;gap:12px}
.screen{width:min(92vw,520px);aspect-ratio:1;background:var(--card);border:2px solid var(--line);border-radius:28px;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center}
.screen.round{border-radius:50%}svg,canvas#sp{width:100%;height:100%}canvas#sp{image-rendering:pixelated;display:none}
.status{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}.cap{min-height:3.2em;width:min(92vw,520px);text-align:center;font-size:18px}
.row{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;align-items:center}
.meter{width:min(92vw,520px);height:14px;background:var(--card);border:1px solid var(--line);border-radius:7px;position:relative;overflow:hidden}
#lvl{height:100%;width:0;background:#4caf7a;transition:width .08s}#thr{position:absolute;top:0;bottom:0;width:2px;background:var(--accent)}
.heard{font-size:14px;opacity:.85;min-height:1.4em}
#face{transform-origin:50% 70%;animation:breathe 4s ease-in-out infinite}@keyframes breathe{50%{transform:scale(1.012)}}
.blink{animation:blink 5s infinite}@keyframes blink{0%,94%,100%{transform:scaleY(1)}97%{transform:scaleY(.08)}}.eyewrap{transform-box:fill-box;transform-origin:center}
.off{filter:grayscale(1) opacity(.55)}.ring{opacity:0;transition:opacity .2s}.listening .ring{opacity:1;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.35}}
.zzz{opacity:0}.sleeping .zzz{opacity:1}.dots{opacity:0}.thinking .dots{opacity:1}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body>
<div class="row"><a href="/editor">Sprite editor</a><button class="alt" id="mode" aria-pressed="false">Showing: sprites</button></div>
<div class="screen" id="screen"><canvas id="sp" aria-label="Robot screen"></canvas>
<svg id="vec" viewBox="0 0 400 400" role="img" aria-label="Robot face"><g id="tails"></g><g id="facepos"><g id="face">
 <g><path d="M105 150 L88 48 L170 105Z" fill="var(--fur)"/><path d="M110 135 L102 75 L152 108Z" fill="var(--cream)"/></g>
 <g><path d="M295 150 L312 48 L230 105Z" fill="var(--fur)"/><path d="M290 135 L298 75 L248 108Z" fill="var(--cream)"/></g>
 <ellipse cx="200" cy="215" rx="125" ry="105" fill="var(--fur)"/><ellipse cx="200" cy="262" rx="62" ry="40" fill="var(--cream)"/>
 <g id="blush" opacity="0"><ellipse cx="120" cy="245" rx="20" ry="11" fill="#ff8f8f" opacity=".55"/><ellipse cx="280" cy="245" rx="20" ry="11" fill="#ff8f8f" opacity=".55"/></g>
 <g id="eyes" class="blink eyewrap"><g id="eyeL"></g><g id="eyeR"></g></g>
 <g stroke="var(--eye)" stroke-width="7" stroke-linecap="round" fill="none"><path id="browL" d="M120 160 Q148 148 176 160"/><path id="browR" d="M224 160 Q252 148 280 160"/></g>
 <ellipse cx="200" cy="243" rx="13" ry="9" fill="var(--eye)"/><path id="mouth" d="M172 268 Q200 290 228 268" stroke="var(--eye)" stroke-width="6" fill="none" stroke-linecap="round"/>
 <g class="ring"><ellipse cx="200" cy="215" rx="150" ry="130" fill="none" stroke="#6aa7ff" stroke-width="5" stroke-dasharray="10 12"/></g>
 <g class="dots" fill="var(--ink)"><circle cx="300" cy="90" r="7"/><circle cx="325" cy="75" r="9"/><circle cx="355" cy="55" r="11"/></g>
 <text class="zzz" x="305" y="95" font-size="38" fill="var(--ink)">z z</text></g></g></svg></div>
<div class="status"><span class="pill" id="s_state">connecting</span><span class="pill" id="s_anim"></span><span class="pill" id="s_pow"></span><span class="pill" id="s_bat"></span><span class="pill" id="s_mic"></span><span class="pill" id="s_stage"></span></div>
<div class="cap" id="cap" aria-live="polite"></div>
<div class="meter" title="microphone level (green) and the speech threshold (red line)"><div id="lvl"></div><div id="thr"></div></div>
<div class="heard" id="heard" aria-live="polite"></div><div class="heard" id="micstat" style="color:#b3261e" role="alert"></div>
<div class="row"><button id="talk">Talk (space)</button><button class="alt" id="mute">Mute mic (m)</button><button class="alt" id="wake">Wake</button>
<label class="pill">battery <input id="bat" type="range" min="0" max="100" value="100"> <span id="batv">100</span>%</label></div>
<div class="note">Test rig only: this is the simulated robot, not the hardware. "You said" shows what she heard, for testing; the real robot shows nothing of the kind.</div>
<script>
const $=id=>document.getElementById(id);
const FACES={idle:{e:'open',m:'smile',b:'n'},happy:{e:'happy',m:'grin',b:'up',blush:1},excited:{e:'wide',m:'open',b:'up',blush:1},celebratory:{e:'happy',m:'open',b:'up',blush:1},
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
const BROW={n:['M120 160 Q148 148 176 160','M224 160 Q252 148 280 160'],up:['M120 146 Q148 130 176 146','M224 146 Q252 130 280 146'],sad:['M120 168 Q148 160 176 150','M224 150 Q252 160 280 168'],angry:['M120 150 L176 170','M224 170 L280 150']};
const TAILS={EGG:0,BABY:1,YOUNG:3,NINE_TAILS:9,PERSONALIZED_NINE_TAILS:9};
function drawTails(n){let s='';if(n===0){s='<ellipse cx="200" cy="245" rx="120" ry="150" fill="var(--cream)" stroke="var(--line)" stroke-width="4"/><circle cx="110" cy="260" r="9" fill="var(--fur)"/><circle cx="285" cy="215" r="11" fill="var(--fur)"/><circle cx="150" cy="335" r="8" fill="var(--fur)"/><circle cx="270" cy="320" r="7" fill="var(--fur)"/>'}
 else{for(let i=0;i<n;i++){const t=n===1?0:(i/(n-1)-.5)*2;const ang=n===1?58:t*62;s+=`<g transform="translate(200 300) rotate(${ang}) scale(.8) translate(-200 -330)"><path d="M200 330 C150 250 160 120 200 60 C240 120 250 250 200 330Z" fill="var(--tail)" opacity=".95"/><path d="M200 120 C182 90 190 70 200 60 C210 70 218 90 200 120Z" fill="var(--tip)"/></g>`}}
 $('tails').innerHTML=s}
const ALPHA='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+-';let talk=0,lastStage='',st={},doc=null,useSprites=true,rev=-1,t0=performance.now();
try{useSprites=localStorage.getItem('nt_mode')!=='vector'}catch(e){}
function showVector(anim,speaking){const f=FACES[anim]||FACES.idle;
 $('screen').className='screen '+(f.cls||'');$('eyeL').innerHTML=eye(150,f.e,f.e==='up'?0:2);$('eyeR').innerHTML=eye(250,f.e,f.e==='up'?0:2);
 const b=BROW[f.b]||BROW.n;$('browL').setAttribute('d',b[0]);$('browR').setAttribute('d',b[1]);$('blush').setAttribute('opacity',f.blush?1:0);
 const sn=speaking||anim==='talking';$('mouth').setAttribute('d',f.m==='talk'||sn?(talk%2?MOUTH.open:MOUTH.smile):MOUTH[f.m]);
 $('mouth').setAttribute('fill',(f.m==='open'||f.m==='grin'||f.m==='o'||(sn&&talk%2))?'#8a2f2f':'none');$('eyes').classList.toggle('blink',f.e==='open'||f.e==='wide')}
function hexRGBA(c){const v=c.slice(1);return[parseInt(v.slice(0,2),16),parseInt(v.slice(2,4),16),parseInt(v.slice(4,6),16),v.length>6?parseInt(v.slice(6,8),16):255]}
function frameAt(a,t){const fr=a.frames;const total=fr.reduce((s,f)=>s+f.ms,0);let x=a.loop===false?Math.min(t,total-1):t%total;for(const f of fr){if(x<f.ms)return f;x-=f.ms}return fr[fr.length-1]}
function showSprite(anim){const cv=$('sp'),a=doc.animations[anim]||doc.animations.idle;if(!a)return;
 const fr=frameAt(a,performance.now()-t0);const W=doc.width,H=doc.height;if(cv.width!==W||cv.height!==H){cv.width=W;cv.height=H}
 const g=cv.getContext('2d');const img=g.createImageData(W,H);const pal=doc.palette.map(hexRGBA);
 for(let y=0;y<H;y++)for(let x=0;x<W;x++){const p=pal[ALPHA.indexOf(fr.rows[y][x])];const i=(y*W+x)*4;img.data[i]=p[0];img.data[i+1]=p[1];img.data[i+2]=p[2];img.data[i+3]=p[3]}
 g.putImageData(img,0,0);$('screen').className='screen '+((doc.screen&&doc.screen.shape==='round')?'round ':'')+(anim==='offline'?'off':'')}
function render(){if(!st.anim)return;const sprites=useSprites&&doc;$('sp').style.display=sprites?'block':'none';$('vec').style.display=sprites?'none':'block';
 $('mode').textContent='Showing: '+(sprites?'sprites':'vector face');$('mode').setAttribute('aria-pressed',String(!!sprites));
 sprites?showSprite(st.anim):showVector(st.anim,st.speaking)}
setInterval(()=>{talk++;render()},100);
async function loadDoc(){try{const r=await fetch('/api/sprites?stage='+encodeURIComponent(st.stage||''));doc=await r.json()}catch(e){doc=null}}
async function poll(){try{const r=await fetch('/api/state');st=await r.json();
 if(st.rev!==rev||st.stage!==stg){rev=st.rev;stg=st.stage;await loadDoc()}
 $('s_state').textContent='she is: '+st.pipe;$('s_anim').textContent=st.anim;$('s_pow').textContent=st.power;$('s_bat').textContent='battery '+st.battery+'%';
 $('s_mic').textContent=st.mic?'mic ON':'mic MUTED';$('s_stage').textContent='stage '+st.stage;$('cap').textContent=st.caption||'';
 $('micstat').textContent=(st.mic_status&&st.mic_status!=='ok')?'MICROPHONE PROBLEM: '+st.mic_status:'';$('lvl').style.width=Math.min(100,(st.level||0)/80)+'%';$('thr').style.left=Math.min(100,(st.threshold||0)/80)+'%';
 if(st.heard!==undefined)$('heard').textContent=st.heard?'You said: '+st.heard:(st.heard_empty?'(she heard nothing)':'');
 if(st.stage!==lastStage){lastStage=st.stage;drawTails(TAILS[st.stage]??1);$('facepos').setAttribute('transform',st.stage==='EGG'?'translate(100 130) scale(.5)':'')}
}catch(e){$('s_state').textContent='disconnected (restart the desk robot)'}setTimeout(poll,100)}
const send=(cmd,arg)=>fetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json','X-NT-Edit':'1'},body:JSON.stringify({cmd,arg})});
$('talk').onclick=()=>send('press');$('mute').onclick=()=>send('mute');$('wake').onclick=()=>send('wake');
$('mode').onclick=()=>{useSprites=!useSprites;try{localStorage.setItem('nt_mode',useSprites?'sprites':'vector')}catch(e){}};
$('bat').oninput=e=>{$('batv').textContent=e.target.value;send('battery',+e.target.value)};
addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;if(e.code==='Space'){e.preventDefault();send('press')}if(e.key==='m')send('mute')});
let stg=null;drawTails(1);loadDoc();poll();
</script></body></html>"""

EDITOR = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nine Tails sprite editor</title><style>""" + _COMMON_CSS + r"""
body{display:grid;gap:12px;grid-template-columns:minmax(0,1fr);max-width:1180px;margin:0 auto}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.main{display:grid;grid-template-columns:auto 1fr;gap:16px}
@media(max-width:900px){.main{grid-template-columns:1fr}}
#cv{border:2px solid var(--line);background:#fff;touch-action:none;image-rendering:pixelated;cursor:crosshair;max-width:100%}
.side{display:flex;flex-direction:column;gap:10px;min-width:260px}.pal{display:flex;flex-wrap:wrap;gap:6px}
.sw{width:30px;height:30px;border-radius:8px;border:2px solid var(--line);cursor:pointer;background-image:linear-gradient(45deg,#ccc 25%,transparent 25%,transparent 75%,#ccc 75%),linear-gradient(45deg,#ccc 25%,#fff 25%,#fff 75%,#ccc 75%);background-size:10px 10px;background-position:0 0,5px 5px;position:relative}
.sw i{position:absolute;inset:0;border-radius:6px}.sw[aria-checked=true]{outline:3px solid var(--ink)}
.frames{display:flex;gap:8px;flex-wrap:wrap}.fr{border:2px solid var(--line);border-radius:8px;background:#fff;padding:2px;cursor:pointer}.fr[aria-selected=true]{border-color:var(--accent);outline:2px solid var(--accent)}
.fr canvas{display:block;image-rendering:pixelated;width:64px;height:64px}
.prev{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}#pv{background:#111;image-rendering:pixelated}
label{font-size:14px}input[type=number]{width:5.5em}.msg{min-height:1.4em}.err{color:#b3261e}.ok{color:#1b7f46}
</style></head><body>
<div class="bar"><a href="/">&larr; Live view</a><b>Sprite editor</b><span class="pill" id="dirty">saved</span>
<button id="save">Save</button><button class="alt" id="reload">Reload from disk</button><button class="alt" id="starter">Load the old built-in face art</button>
<button class="alt" id="dl">Download .json</button><label class="pill">Open .json <input id="up" type="file" accept=".json,application/json"></label><span class="msg" id="msg" role="status"></span></div>
<div class="main"><div><canvas id="cv" tabindex="0" aria-label="Pixel canvas"></canvas>
 <div class="bar" style="margin-top:8px"><button class="alt" data-tool="pencil" aria-pressed="true">Pencil (b)</button><button class="alt" data-tool="eraser" aria-pressed="false">Eraser (e)</button>
 <button class="alt" data-tool="fill" aria-pressed="false">Fill (g)</button><button class="alt" data-tool="pick" aria-pressed="false">Pick colour (i)</button>
 <label class="pill"><input type="checkbox" id="mirror"> mirror left/right (m)</label><label class="pill"><input type="checkbox" id="onion" checked> onion skin</label>
 <label class="pill"><input type="checkbox" id="grid" checked> grid</label><button class="alt" id="undo">Undo (ctrl+z)</button><button class="alt" id="redo">Redo</button></div></div>
<div class="side">
 <label>Animation <select id="anim"></select></label><label class="pill"><input type="checkbox" id="loop"> loops</label>
 <div><b>Frames</b> <span class="note">(click one to edit)</span><div class="frames" id="frames"></div>
 <div class="bar" style="margin-top:6px"><button class="alt" id="addf">+ copy</button><button class="alt" id="delf">Delete</button><button class="alt" id="lf">&larr; move</button><button class="alt" id="rf">move &rarr;</button>
 <label>ms <input type="number" id="ms" min="40" max="10000" step="10"></label></div></div>
 <div><b>Colours</b> <span class="note">(0 is see-through, up to 64)</span><div class="pal" id="pal" role="radiogroup" aria-label="Palette"></div>
 <div class="bar" style="margin-top:6px"><label>Edit selected <input type="color" id="col"></label><button class="alt" id="addc">+ colour</button></div></div>
 <div><b>Screen preview</b><div class="bar"><label>width <input type="number" id="sw" min="32" max="2048"></label><label>height <input type="number" id="sh" min="32" max="2048"></label>
 <label>shape <select id="shape"><option value="square">square</option><option value="round">round</option></select></label></div>
 <div class="prev"><canvas id="pv"></canvas><div class="note" id="pvinfo"></div></div>
 <div class="note">The real screen size is UNVERIFIED until the board is known. Set it here and the preview follows; the art itself stays at its own size.</div></div>
</div></div>
<script>
const $=id=>document.getElementById(id);const HEX='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+-';
let doc=null,anim='idle',fi=0,tool='pencil',color=1,undo=[],redo=[],dirty=false,drawing=false,Z=10,playT0=performance.now();
const hexRGBA=c=>{const v=c.slice(1);return[parseInt(v.slice(0,2),16),parseInt(v.slice(2,4),16),parseInt(v.slice(4,6),16),v.length>6?parseInt(v.slice(6,8),16):255]};
const rowsOf=()=>doc.animations[anim].frames[fi].rows;
function msg(t,cls){const m=$('msg');m.textContent=t||'';m.className='msg '+(cls||'')}
function setDirty(d){dirty=d;$('dirty').textContent=d?'UNSAVED changes':'saved'}
const send=(m,url,body)=>fetch(url,{method:m,headers:{'Content-Type':'application/json','X-NT-Edit':'1'},body:body===undefined?undefined:JSON.stringify(body)});
async function load(url){const r=await fetch(url||'/api/sprites');doc=await r.json();anim=doc.animations[anim]?anim:Object.keys(doc.animations)[0];fi=0;undo=[];redo=[];color=Math.min(color,doc.palette.length-1);setDirty(false);build()}
function build(){const sel=$('anim');sel.innerHTML='';const names=new Set(Object.keys(doc.animations));
 Object.keys(doc.animations).sort().forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o)});
 ['affectionate','angry','calm','celebratory','confused','curious','eating','excited','happy','idle','listening','offline','playing','reflective','sad','sleeping','sleepy','surprised','talking','thinking','waking'].filter(n=>!names.has(n)).forEach(n=>{const o=document.createElement('option');o.value='+'+n;o.textContent='+ add '+n;sel.appendChild(o)});
 sel.value=anim;$('loop').checked=doc.animations[anim].loop!==false;$('sw').value=(doc.screen||{}).width||240;$('sh').value=(doc.screen||{}).height||240;$('shape').value=(doc.screen||{}).shape||'square';
 const cv=$('cv');cv.width=doc.width*Z;cv.height=doc.height*Z;cv.style.width=Math.min(doc.width*Z,640)+'px';cv.style.height='auto';buildPal();buildFrames();draw()}
function buildPal(){const p=$('pal');p.innerHTML='';doc.palette.forEach((c,i)=>{const b=document.createElement('div');b.className='sw';b.setAttribute('role','radio');b.setAttribute('aria-checked',String(i===color));b.setAttribute('aria-label','colour '+i);b.tabIndex=0;
 b.innerHTML='<i style="background:'+c+'"></i>';b.onclick=()=>{color=i;buildPal()};b.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){color=i;buildPal()}};p.appendChild(b)});$('col').value=doc.palette[color].slice(0,7)}
function paint(g,rows,w,h,alpha){const pal=doc.palette.map(hexRGBA);const img=g.createImageData(w,h);for(let y=0;y<h;y++)for(let x=0;x<w;x++){const q=pal[HEX.indexOf(rows[y][x])];const i=(y*w+x)*4;img.data[i]=q[0];img.data[i+1]=q[1];img.data[i+2]=q[2];img.data[i+3]=q[3]*(alpha??1)}return img}
function thumb(rows){const c=document.createElement('canvas');c.width=doc.width;c.height=doc.height;c.getContext('2d').putImageData(paint(c.getContext('2d'),rows,doc.width,doc.height),0,0);return c}
function buildFrames(){const f=$('frames');f.innerHTML='';doc.animations[anim].frames.forEach((fr,i)=>{const d=document.createElement('div');d.className='fr';d.setAttribute('aria-selected',String(i===fi));d.title='frame '+(i+1)+', '+fr.ms+' ms';d.tabIndex=0;d.appendChild(thumb(fr.rows));d.onclick=()=>{fi=i;buildFrames();draw()};d.onkeydown=e=>{if(e.key==='Enter'){fi=i;buildFrames();draw()}};f.appendChild(d)});$('ms').value=doc.animations[anim].frames[fi].ms}
function draw(){const cv=$('cv'),W=doc.width,H=doc.height;const off=document.createElement('canvas');off.width=W;off.height=H;const og=off.getContext('2d');
 const g=cv.getContext('2d');g.imageSmoothingEnabled=false;g.clearRect(0,0,cv.width,cv.height);
 for(let y=0;y<H;y++)for(let x=0;x<W;x++){g.fillStyle=(x+y)%2?'#eee':'#fff';g.fillRect(x*Z,y*Z,Z,Z)}
 const frs=doc.animations[anim].frames;if($('onion').checked&&frs.length>1){const pr=frs[(fi+frs.length-1)%frs.length];og.putImageData(paint(og,pr.rows,W,H,.3),0,0);g.drawImage(off,0,0,W*Z,H*Z);og.clearRect(0,0,W,H)}
 og.putImageData(paint(og,frs[fi].rows,W,H),0,0);g.drawImage(off,0,0,W*Z,H*Z);
 if($('grid').checked){g.strokeStyle='rgba(0,0,0,.12)';g.lineWidth=1;g.beginPath();for(let x=0;x<=W;x++){g.moveTo(x*Z+.5,0);g.lineTo(x*Z+.5,H*Z)}for(let y=0;y<=H;y++){g.moveTo(0,y*Z+.5);g.lineTo(W*Z,y*Z+.5)}g.stroke()}
 if($('mirror').checked){g.strokeStyle='rgba(196,85,45,.5)';g.beginPath();g.moveTo(W*Z/2,0);g.lineTo(W*Z/2,H*Z);g.stroke()}}
function snapshot(){undo.push(JSON.stringify(rowsOf()));if(undo.length>100)undo.shift();redo=[]}
function restore(a,b){if(!a.length)return;b.push(JSON.stringify(rowsOf()));doc.animations[anim].frames[fi].rows=JSON.parse(a.pop());setDirty(true);buildFrames();draw()}
function setPx(x,y,c){if(x<0||y<0||x>=doc.width||y>=doc.height)return;const rows=rowsOf();const r=rows[y];const ch=HEX[c];if(r[x]!==ch)rows[y]=r.slice(0,x)+ch+r.slice(x+1)}
function apply(x,y){const c=tool==='eraser'?0:color;const xs=[x];if($('mirror').checked)xs.push(doc.width-1-x);
 if(tool==='pick'){color=HEX.indexOf(rowsOf()[y][x]);buildPal();setTool('pencil');return}
 if(tool==='fill'){const target=rowsOf()[y][x];if(target===HEX[c])return;xs.forEach(px=>flood(px,y,target,c));return}
 xs.forEach(px=>setPx(px,y,c))}
function flood(x,y,target,c){const W=doc.width,H=doc.height,st=[[x,y]];const rows=rowsOf();while(st.length){const[a,b]=st.pop();if(a<0||b<0||a>=W||b>=H||rows[b][a]!==target)continue;setPx(a,b,c);st.push([a+1,b],[a-1,b],[a,b+1],[a,b-1])}}
function cell(e){const r=$('cv').getBoundingClientRect();return[Math.floor((e.clientX-r.left)/r.width*doc.width),Math.floor((e.clientY-r.top)/r.height*doc.height)]}
let last=null;const cvEl=$('cv');
cvEl.addEventListener('pointerdown',e=>{cvEl.setPointerCapture(e.pointerId);drawing=true;snapshot();const[x,y]=cell(e);last=[x,y];apply(x,y);setDirty(true);draw()});
cvEl.addEventListener('pointermove',e=>{if(!drawing||tool==='fill'||tool==='pick')return;const[x,y]=cell(e);if(last&&(x!==last[0]||y!==last[1])){const n=Math.max(Math.abs(x-last[0]),Math.abs(y-last[1]));for(let i=1;i<=n;i++)apply(Math.round(last[0]+(x-last[0])*i/n),Math.round(last[1]+(y-last[1])*i/n));last=[x,y];draw()}});
const end=()=>{if(drawing){drawing=false;last=null;buildFrames()}};cvEl.addEventListener('pointerup',end);cvEl.addEventListener('pointercancel',end);
function setTool(t){tool=t;document.querySelectorAll('[data-tool]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tool===t)))}
document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));
$('undo').onclick=()=>restore(undo,redo);$('redo').onclick=()=>restore(redo,undo);
['onion','grid','mirror'].forEach(i=>$(i).onchange=draw);
$('anim').onchange=e=>{let v=e.target.value;if(v[0]==='+'){const n=v.slice(1);const src=doc.animations.idle||Object.values(doc.animations)[0];doc.animations[n]=JSON.parse(JSON.stringify(src));v=n;setDirty(true)}anim=v;fi=0;undo=[];redo=[];build()};
$('loop').onchange=e=>{doc.animations[anim].loop=e.target.checked;setDirty(true)};
$('ms').onchange=e=>{const v=Math.max(40,Math.min(10000,parseInt(e.target.value)||40));doc.animations[anim].frames[fi].ms=v;setDirty(true);buildFrames()};
$('addf').onclick=()=>{const fs=doc.animations[anim].frames;if(fs.length>=12){msg('At most 12 frames per animation.','err');return}fs.splice(fi+1,0,JSON.parse(JSON.stringify(fs[fi])));fi++;setDirty(true);buildFrames();draw()};
$('delf').onclick=()=>{const fs=doc.animations[anim].frames;if(fs.length<2){msg('An animation needs at least one frame.','err');return}fs.splice(fi,1);fi=Math.max(0,fi-1);setDirty(true);buildFrames();draw()};
const mv=d=>{const fs=doc.animations[anim].frames;const j=fi+d;if(j<0||j>=fs.length)return;[fs[fi],fs[j]]=[fs[j],fs[fi]];fi=j;setDirty(true);buildFrames();draw()};$('lf').onclick=()=>mv(-1);$('rf').onclick=()=>mv(1);
$('col').oninput=e=>{if(color===0){msg('Colour 0 stays see-through.');return}doc.palette[color]=e.target.value;setDirty(true);buildPal();buildFrames();draw()};
$('addc').onclick=()=>{if(doc.palette.length>=64){msg('At most 64 colours.','err');return}doc.palette.push('#ff00ff');color=doc.palette.length-1;setDirty(true);buildPal()};
['sw','sh','shape'].forEach(i=>$(i).onchange=()=>{doc.screen={width:parseInt($('sw').value)||240,height:parseInt($('sh').value)||240,shape:$('shape').value};setDirty(true)});
$('save').onclick=async()=>{const r=await send('PUT','/api/sprites',doc);const j=await r.json();if(r.ok){setDirty(false);msg('Saved to assets/sprites/. Commit it with git when you are happy.','ok')}else msg('Not saved: '+j.error,'err')};
$('reload').onclick=()=>{if(dirty&&!confirm('Throw away your unsaved changes?'))return;load().then(()=>msg('Reloaded.'))};
$('starter').onclick=()=>{if(!confirm('Replace everything on screen with the old built-in face art? (Nothing is saved until you press Save.)'))return;load('/api/sprites/default').then(()=>{setDirty(true);msg('Old face art loaded. Press Save to keep it.')})};
$('dl').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(doc,null,1)],{type:'application/json'}));a.download=doc.name+'.json';a.click()};
$('up').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{const d=JSON.parse(await f.text());doc=d;anim=Object.keys(d.animations)[0];fi=0;undo=[];redo=[];setDirty(true);build();msg('Opened '+f.name+' (the server checks it when you Save).')}catch(x){msg('That is not a sprite file.','err')}};
addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;const k=e.key.toLowerCase();
 if((e.ctrlKey||e.metaKey)&&k==='z'){e.preventDefault();restore(undo,redo)}else if((e.ctrlKey||e.metaKey)&&k==='y'){e.preventDefault();restore(redo,undo)}
 else if(k==='b')setTool('pencil');else if(k==='e')setTool('eraser');else if(k==='g')setTool('fill');else if(k==='i')setTool('pick');else if(k==='m'){$('mirror').checked=!$('mirror').checked;draw()}});
addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
function preview(){if(!doc)return requestAnimationFrame(preview);const pv=$('pv'),s=doc.screen||{width:240,height:240,shape:'square'};const a=doc.animations[anim];
 const SW=Math.min(s.width,480),SH=Math.min(s.height,480),k=Math.min(480/s.width,480/s.height,1);const dw=Math.round(s.width*k),dh=Math.round(s.height*k);
 if(pv.width!==dw||pv.height!==dh){pv.width=dw;pv.height=dh}const g=pv.getContext('2d');g.imageSmoothingEnabled=false;g.fillStyle='#fff4e3';g.fillRect(0,0,dw,dh);
 const scale=Math.max(1,Math.floor(Math.min(s.width/doc.width,s.height/doc.height)));const total=a.frames.reduce((t,f)=>t+f.ms,0);let x=(performance.now()-playT0)%total,fr=a.frames[a.frames.length-1];for(const f of a.frames){if(x<f.ms){fr=f;break}x-=f.ms}
 const off=document.createElement('canvas');off.width=doc.width;off.height=doc.height;off.getContext('2d').putImageData(paint(off.getContext('2d'),fr.rows,doc.width,doc.height),0,0);
 g.save();if(s.shape==='round'){g.beginPath();g.ellipse(dw/2,dh/2,dw/2,dh/2,0,0,7);g.clip()}const w=doc.width*scale*k,h=doc.height*scale*k;g.drawImage(off,(dw-w)/2,(dh-h)/2,w,h);g.restore();
 $('pvinfo').textContent=`${doc.width}x${doc.height} sprite on a ${s.width}x${s.height} ${s.shape} screen: ${scale}x whole-pixel zoom (${doc.width*scale}x${doc.height*scale} px), margin ${Math.max(0,s.width-doc.width*scale)/2}x${Math.max(0,s.height-doc.height*scale)/2} px. Playing: ${anim}`;
 requestAnimationFrame(preview)}
load().then(()=>{preview();msg('Draw on the big grid. Turn on mirror (m) only for symmetric art; the angled views are not symmetric.')}).catch(()=>msg('Could not load the sprites.','err'));
</script></body></html>"""

if __name__ == "__main__":
    import sys
    sys.exit(main())
