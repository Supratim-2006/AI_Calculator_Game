import asyncio, json, logging, secrets, time
from contextlib import asynccontextmanager
import jwt
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .core import config
from .core.config import cfg
from .services import store
from .services.game_engine import Team, Player, engine as E, InvalidTransition, ev

logging.basicConfig(level=logging.INFO, format="%(message)s")

@asynccontextmanager
async def lifespan(_):
    await store.init(); await store.load_into(E)
    tasks = [asyncio.create_task(E.run()), asyncio.create_task(store.flusher(E))]
    yield
    for t in tasks: t.cancel()
    await store.flush(E)

app = FastAPI(title="AI Calculator Arena", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

def err(msg, code): return JSONResponse({"error": msg}, status_code=code)
def mint(**c): return jwt.encode({**c, "exp": time.time() + 12 * 3600}, config.JWT_SECRET, algorithm="HS256")
def decode(tok):
    try: return jwt.decode(tok, config.JWT_SECRET, algorithms=["HS256"])
    except Exception: return None

class Join(BaseModel): code: str = Field(max_length=8); name: str = Field(min_length=1, max_length=20)
class Login(BaseModel): pass_: str = Field(alias="pass")
class Names(BaseModel): names: list[str]

# ---------- players ----------
@app.post("/api/join")
async def join(b: Join):
    t = next((x for x in E.teams.values() if x.code == b.code.upper()), None)
    if not t: return err("Unknown team code", 404)
    p = next((x for x in t.players if x.name.lower() == b.name.strip().lower()), None)   # same name = reconnect to same seat
    if not p:
        if len(t.players) >= 3: return err("Team already has 3 players", 409)
        p = Player("P" + secrets.token_hex(2), b.name.strip()); t.players.append(p); t.roles["XYZ"[len(t.players) - 1]] = p.id
        ev("team_joined", team_id=t.id, player_id=p.id)
        if len(t.players) == 3 and t.phase == "LOBBY": await E.next(t)
        else: await E.push(t)
    return {"token": mint(sub=p.id, team=t.id, role="PLAYER"), "team_id": t.id}

@app.get("/api/team/{tid}/question")
async def my_question(tid: str, authorization: str = Header("")):
    c = decode(authorization.removeprefix("Bearer "))
    if not c or c.get("role") != "PLAYER": return err("unauthorized", 401)
    if c["team"] != tid: return err("forbidden", 403)           # Team A can never read Team B
    return E.view(E.teams[tid], c["sub"])["question"]

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
@app.post("/api/admin/teams")
async def create_teams(b: Names, x_admin: str = Header("")):
    if not is_admin(x_admin): return err("unauthorized", 401)
    out = []
    for n in b.names:
        t = Team("T" + secrets.token_hex(2).upper(), n[:30], secrets.token_hex(3).upper()); E.teams[t.id] = t; E.dirty.add(t.id)
        out.append({"id": t.id, "name": t.name, "code": t.code}); ev("admin_action", action="create_team", team_id=t.id)
    return out
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
    ev("admin_action", action="next", team_id=tid); return JSONResponse(None, status_code=204)
@app.get("/api/leaderboard")
async def leaderboard(): return sorted(({"team": t.name, "score": t.score} for t in E.teams.values()), key=lambda r: -r["score"])
@app.get("/api/admin/state")
async def state(x_admin: str = Header("")):
    if not is_admin(x_admin): return err("unauthorized", 401)
    ts = list(E.teams.values())
    rows = [{"id": t.id, "name": t.name, "code": t.code, "phase": t.phase, "score": t.score, "remaining": t.remaining(), "values": t.values, "log": t.log,
             "players": [{"name": p.name, "online": p.ws is not None, "role": t.role_of(p.id)} for p in t.players],
             "question": t.q and {"expression": t.q.display, "target": t.q.target, "difficulty": t.q.level, "constraint": t.q.con,
                                   "solutions": len(t.q.solutions), "seed": t.q.seed, "id": t.q.id}} for t in ts]
    return {"total": len(ts), "online": sum(any(p.ws for p in t.players) for t in ts),
            "playing": sum(t.phase in ("ASSIGN", "COUNTDOWN", "PLAYING", "RESULT") for t in ts),
            "finished": sum(t.phase == "FINISHED" for t in ts), "teams": sorted(rows, key=lambda r: -r["score"])}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
