import asyncio, json, logging, re, secrets, time
from contextlib import asynccontextmanager
import jwt
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .core import config
from .core.config import cfg
from .services import store
from .services.game_engine import Team, Player, engine as E, InvalidTransition, ev

logging.basicConfig(level=logging.INFO, format="%(message)s")

@asynccontextmanager
async def lifespan(_):
    try: await store.init(); await store.load_into(E)
    except Exception: logging.getLogger("arena").exception("DATABASE UNREACHABLE at startup: running in memory, will keep retrying saves")
    tasks = [asyncio.create_task(E.run()), asyncio.create_task(store.flusher(E))]
    yield
    for t in tasks: t.cancel()
    await store.flush(E)

app = FastAPI(title="AI Calculator Arena", lifespan=lifespan)
@app.middleware("http")
async def no_cache(request, call_next):   # the browser must never cache pages or API answers
    r = await call_next(request); r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"; r.headers["Pragma"] = "no-cache"; return r
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

def err(msg, code): return JSONResponse({"error": msg}, status_code=code)
def mint(**c): return jwt.encode({**c, "exp": time.time() + 12 * 3600}, config.JWT_SECRET, algorithm="HS256")
def decode(tok):
    try: return jwt.decode(tok, config.JWT_SECRET, algorithms=["HS256"])
    except Exception: return None

class Reg(BaseModel):
    team_name: str = Field(min_length=1, max_length=30); name: str = Field(min_length=1, max_length=20)
    roll: str = Field(min_length=1, max_length=30); phone: str = Field(min_length=7, max_length=20)
class Join(BaseModel):
    code: str = Field(max_length=8); name: str = Field(min_length=1, max_length=20)
    roll: str = Field(min_length=1, max_length=30); phone: str = Field(min_length=7, max_length=20)
class Login(BaseModel): pass_: str = Field(alias="pass")

def clean_phone(p):
    p = re.sub(r"[\s\-()]", "", p); return p if re.fullmatch(r"\+?\d{7,15}", p) else None
def find_roll(roll): return next(((t, p) for t in E.teams.values() for p in t.players if p.roll.lower() == roll.lower()), None)

# ---------- players ----------
@app.post("/api/register")                                       # the team LEADER creates the team and receives the team ID (code)
async def register(b: Reg):
    tn, name, roll, phone = b.team_name.strip(), b.name.strip(), b.roll.strip(), clean_phone(b.phone)
    if not (tn and name and roll): return err("Please fill in every field", 422)
    if not phone: return err("Enter a valid phone number (7 to 15 digits)", 422)
    if any(t.name.lower() == tn.lower() for t in E.teams.values()): return err("That team name is already taken", 409)
    if find_roll(roll): return err("This roll number is already registered", 409)
    code = secrets.token_hex(3).upper()
    while any(t.code == code for t in E.teams.values()): code = secrets.token_hex(3).upper()
    t = Team("T" + secrets.token_hex(2).upper(), tn, code); p = Player("P" + secrets.token_hex(2), name, roll, phone)
    t.players.append(p); t.roles["X"] = p.id; t.leader = p.id; E.teams[t.id] = t
    ev("team_registered", team_id=t.id, player_id=p.id); await E.push(t)
    return {"token": mint(sub=p.id, team=t.id, role="PLAYER"), "team_id": t.id, "code": code}

@app.post("/api/join")                                           # teammates (and a returning leader) log in with the team ID + their own details
async def join(b: Join):
    t = next((x for x in E.teams.values() if x.code == b.code.strip().upper()), None)
    if not t: return err("Unknown team ID", 404)
    name, roll, phone = b.name.strip(), b.roll.strip(), clean_phone(b.phone)
    if not (name and roll): return err("Please fill in every field", 422)
    if not phone: return err("Enter a valid phone number (7 to 15 digits)", 422)
    p = next((x for x in t.players if x.roll.lower() == roll.lower()), None)
    if p:                                                        # same roll number = reconnect to the same seat, but only with matching details
        if p.phone != phone or p.name.lower() != name.lower(): return err("These details do not match the registration for this roll number", 403)
    else:
        if find_roll(roll): return err("This roll number is already registered in a team", 409)
        if len(t.players) >= 3: return err("Team already has 3 players", 409)
        p = Player("P" + secrets.token_hex(2), name, roll, phone); t.players.append(p); t.roles["XYZ"[len(t.players) - 1]] = p.id
        ev("team_joined", team_id=t.id, player_id=p.id)
        if len(t.players) == 3 and t.phase == "LOBBY": await E.next(t)
        else: await E.push(t)
    return {"token": mint(sub=p.id, team=t.id, role="PLAYER"), "team_id": t.id}

@app.get("/api/team/{tid}/question")
async def my_question(tid: str, authorization: str = Header("")):
    c = decode(authorization.removeprefix("Bearer "))
    if not c or c.get("role") != "PLAYER": return err("unauthorized", 401)
    if c["team"] != tid: return err("forbidden", 403)           # Team A can never read Team B
    return E.view(E.teams[tid], c["sub"])["question"] if tid in E.teams else err("not found", 404)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    await ws.accept(); c = decode(token)
    t = c and c.get("role") == "PLAYER" and E.teams.get(c["team"]); p = t and next((x for x in t.players if x.id == c["sub"]), None)
    if not p: return await ws.close(4401)
    if p.ws:                                                       # one active session per player
        try: await p.ws.close(4401)
        except Exception: pass
    p.ws = ws; ev("player_connected", team_id=t.id, player_id=p.id); await E.push(t)   # reconnect restores role/question/time
    try:
        while True:
            try: m = json.loads(await ws.receive_text())
            except json.JSONDecodeError: continue
            if isinstance(m, dict):
                try: await E.handle(t, p, m)
                except InvalidTransition: pass
    except WebSocketDisconnect: pass
    finally:
        if p.ws is ws: p.ws = None; ev("player_disconnected", team_id=t.id, player_id=p.id); await E.push(t)

# ---------- admin (JWT role=ADMIN in x-admin header) ----------
def is_admin(tok): c = decode(tok or ""); return bool(c and c.get("role") == "ADMIN")
@app.post("/api/admin/login")
async def admin_login(b: Login):
    return {"token": mint(sub="admin", role="ADMIN")} if secrets.compare_digest(b.pass_, config.ADMIN_PASS) else err("bad password", 401)
@app.get("/api/admin/config")
async def get_cfg(x_admin: str = Header("")): return cfg if is_admin(x_admin) else err("unauthorized", 401)
@app.put("/api/admin/config")
async def put_cfg(body: dict, x_admin: str = Header("")):
    if not is_admin(x_admin): return err("unauthorized", 401)
    for k, v in body.items():
        if k in cfg: cfg[k] = {int(a): b for a, b in v.items()} if isinstance(v, dict) else v   # JSON keys arrive as strings
    ev("admin_action", action="config"); return cfg
@app.post("/api/admin/team/{tid}/next")
async def force_next(tid: str, x_admin: str = Header("")):
    t = E.teams.get(tid)
    if not is_admin(x_admin): return err("unauthorized", 401)
    if not t or len(t.players) < 3: return err("team not ready", 400)
    try: await E.next(t)
    except InvalidTransition: return err("team finished", 409)
    ev("admin_action", action="next", team_id=tid); return Response(status_code=204)
@app.delete("/api/admin/team/{tid}")
async def delete_team(tid: str, x_admin: str = Header("")):
    if not is_admin(x_admin): return err("unauthorized", 401)
    t = E.teams.pop(tid, None)
    if not t: return err("team not found", 404)
    E.dirty.discard(tid)
    for p in t.players:                                          # kick anyone still connected; their token is now useless
        if p.ws:
            try: await p.ws.close(4401)
            except Exception: pass
    await store.delete_team(tid); ev("admin_action", action="delete_team", team_id=tid); return Response(status_code=204)
@app.get("/api/leaderboard")
async def leaderboard(): return sorted(({"team": t.name, "score": t.score} for t in E.teams.values()), key=lambda r: -r["score"])
@app.get("/api/admin/state")
async def state(x_admin: str = Header("")):
    if not is_admin(x_admin): return err("unauthorized", 401)
    ts = list(E.teams.values())
    rows = [{"id": t.id, "name": t.name, "code": t.code, "phase": t.phase, "score": t.score, "remaining": t.remaining(), "values": t.values, "log": t.log,
             "paused": t.paused, "players": [{"name": p.name, "roll": p.roll, "phone": p.phone, "leader": p.id == t.leader, "online": p.ws is not None, "role": t.role_of(p.id)} for p in t.players],
             "question": t.q and {"expression": t.q.display, "target": t.q.target, "difficulty": t.q.level, "constraint": t.q.con,
                                   "solutions": len(t.q.solutions), "seed": t.q.seed, "id": t.q.id}} for t in ts]
    return {"total": len(ts), "online": sum(any(p.ws for p in t.players) for t in ts),
            "playing": sum(t.phase in ("ASSIGN", "COUNTDOWN", "PLAYING", "RESULT") for t in ts),
            "finished": sum(t.phase == "FINISHED" for t in ts), "teams": sorted(rows, key=lambda r: -r["score"])}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
