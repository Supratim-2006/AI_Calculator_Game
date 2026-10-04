"""Authoritative game engine: per-team state machine, server clock, validation, scoring."""
import asyncio, json, logging, math, time
from .question_generator import QuestionGenerator, satisfies, evaluate, LO, HI
from .scoring import correct_points
from ..core.config import cfg, QUESTION_LOG

log = logging.getLogger("arena")
def ev(event, **kw): log.info(json.dumps({"ts": time.time(), "event": event, **kw}))

# RESULT is legacy (old saved games); the live flow never enters it: a question ends and the next one starts at once.
ALLOWED = {"LOBBY": {"ASSIGN"}, "ASSIGN": {"COUNTDOWN", "PLAYING", "FINISHED"}, "COUNTDOWN": {"PLAYING", "FINISHED"},
           "PLAYING": {"PLAYING", "FINISHED"}, "RESULT": {"ASSIGN", "PLAYING", "FINISHED"}, "FINISHED": set()}
class InvalidTransition(Exception): pass

class Player:
    def __init__(self, pid, name, roll="", phone=""): self.id, self.name, self.roll, self.phone, self.ws = pid, name, roll, phone, None
class Team:
    def __init__(self, tid, name, code):
        self.id, self.name, self.code = tid, name, code
        self.players, self.roles, self.values, self.history, self.log = [], {}, {}, set(), []
        self.phase, self.q, self.n, self.score, self.attempts, self.qpts = "LOBBY", None, 0, 0, 0, 0
        self.deadline, self.res, self.last_rem = 0.0, None, -1
        self.leader, self.hold = None, None   # leader = player id allowed to press START; hold = seconds left while the game is paused (someone offline)
        self.last, self.checked = None, None   # last = toast about the question that just ended; checked = (X,Y,Z) already judged
        self.changed = 0.0
    def remaining(self):
        if self.hold is not None: return math.ceil(self.hold)                 # paused: the clock is frozen
        return max(0, math.ceil(self.deadline - time.monotonic())) if self.deadline else 0
    @property
    def paused(self): return self.hold is not None
    def all_online(self): return len(self.players) == 3 and all(p.ws for p in self.players)
    def role_of(self, pid): return next((r for r, p in self.roles.items() if p == pid), None)

class Engine:
    def __init__(self):
        self.teams: dict[str, Team] = {}
        self.gen = QuestionGenerator(cfg, QUESTION_LOG)
        self.dirty = set()

    def go(self, t, phase):
        if phase not in ALLOWED[t.phase]: raise InvalidTransition(f"{t.phase} -> {phase}")
        t.phase = phase

    def sync(self, t):
        """Pause the clock while any team member is offline and resume it when everybody is back. Returns True if the state changed."""
        live = t.phase in ("COUNTDOWN", "PLAYING"); now = time.monotonic()
        if live and not t.all_online() and t.hold is None and t.deadline:
            t.hold = max(0.0, t.deadline - now); t.deadline = 0.0; ev("game_paused", team_id=t.id); return True
        if t.hold is not None and (not live or t.all_online()):
            if live: t.deadline = now + t.hold; t.changed = now; ev("game_resumed", team_id=t.id)
            t.hold = None; return True
        return False

    async def push(self, t, save=True):
        self.sync(t)
        if save: self.dirty.add(t.id)
        msgs = [(p.ws, json.dumps(self.view(t, p.id))) for p in t.players if p.ws]
        await asyncio.gather(*(w.send_text(m) for w, m in msgs), return_exceptions=True)

    def view(self, t, pid):  # the ONLY payload clients get: no solutions, no seed
        q = t.q
        return {"team_id": t.id, "team": t.name, "phase": t.phase, "score": t.score, "log": t.log, "attempts": t.attempts,
                "remaining": t.remaining(), "values": t.values, "res": t.res, "n": t.n, "total": len(cfg["sequence"]), "locking": t.phase == "PLAYING" and all(k in t.values for k in "XYZ"), "last": t.last, "code": t.code, "paused": t.paused, "leader": t.leader,
                "players": [{"id": p.id, "name": p.name, "online": p.ws is not None, "role": t.role_of(p.id), "leader": p.id == t.leader} for p in t.players],
                "you": {"id": pid, "role": t.role_of(pid), "leader": pid == t.leader},
                "question": q and {"question_id": q.id, "expression": q.display, "target": q.target,
                                   "difficulty": q.level, "time_limit": q.time_limit, "constraint": q.con}}

    async def next(self, t, last=None, record=True):
        """Move the team to its next question immediately (no result screen, no delay).
        record=True logs the question we are leaving as 0 points (timer ran out / admin skip)."""
        if t.phase == "FINISHED": raise InvalidTransition("finished")
        if record and t.q and t.phase in ("ASSIGN", "COUNTDOWN", "PLAYING"):
            t.log.append({"n": t.n, "pts": 0, "ok": False}); last = last or {"ok": False, "timeUp": True}
        t.hold = None; t.last = last; t.n += 1; t.values, t.res, t.attempts, t.qpts, t.checked = {}, None, 0, 0, None
        if t.n > len(cfg["sequence"]): t.q = None; t.deadline = 0.0; self.go(t, "FINISHED")
        else:
            first = t.phase == "LOBBY"
            t.q = self.gen.generate_question(t.id, t.n, cfg["sequence"][t.n - 1], t.history)
            if first: self.go(t, "ASSIGN"); t.deadline = 0.0                  # first question waits for the START button
            else: self.go(t, "PLAYING"); t.deadline = time.monotonic() + t.q.time_limit; t.last_rem = -1; t.changed = time.monotonic()
            ev("question_generated", team_id=t.id, question_id=t.q.id, difficulty=t.q.level)
        await self.push(t)

    async def handle(self, t, p, m):
        typ, role = m.get("type"), t.role_of(p.id)
        if typ == "role" and t.phase == "ASSIGN" and m.get("role") in ("X", "Y", "Z"):
            r = m["role"]
            if r != role: other = t.roles[r]; t.roles[r] = p.id; t.roles[role] = other   # swap keeps a valid permutation
            await self.push(t)
        elif typ == "start" and t.phase == "ASSIGN" and p.id == t.leader and t.all_online() and len(set(t.roles.values())) == 3:   # leader only, everyone online
            self.go(t, "COUNTDOWN"); t.deadline = time.monotonic() + cfg["countdown"]; await self.push(t)  # roles locked
        elif typ == "digit" and t.phase == "PLAYING" and role and not t.paused:
            d, c = m.get("digit"), m.get("conf")
            if d is None:                                                  # hand left the camera: clear this player's value
                if role in t.values: del t.values[role]; t.changed = time.monotonic(); await self.push(t)
            elif type(d) is int and LO <= d <= HI and isinstance(c, (int, float)) and c >= cfg["minConf"] and t.values.get(role) != d:
                t.values[role] = d; t.changed = time.monotonic(); await self.push(t)

    async def check_answer(self, t):   # automatic: runs once X, Y and Z are all present and stable for lockSeconds (no submit button)
        x, y, z, q = t.values["X"], t.values["Y"], t.values["Z"], t.q
        t.checked = (x, y, z)           # judge each combination once; a wrong one is silent and the team simply keeps trying until the timer ends
        if satisfies(q, x, y, z):
            pts, bonus = correct_points(q.level, t.remaining(), cfg)
            t.log.append({"n": t.n, "pts": pts, "ok": True}); t.score += pts
            ev("answer_correct", team_id=t.id, question_id=q.id, pts=pts)
            await self.next(t, {"ok": True, "pts": pts, "bonus": bonus, "X": x, "Y": y, "Z": z}, record=False)   # straight to the next question
        else: ev("answer_wrong", team_id=t.id, question_id=q.id)

    async def run(self):  # server-authoritative clock
        while True:
            await asyncio.sleep(0.25); now = time.monotonic()
            for t in list(self.teams.values()):
                try:
                    if self.sync(t): await self.push(t, save=False)            # pause / resume when a player drops or returns
                    if t.paused: continue                                      # frozen: no timeout, no answer checking
                    due, rem = t.deadline and now >= t.deadline, t.remaining()
                    if t.phase == "COUNTDOWN" and due: self.go(t, "PLAYING"); t.deadline = now + t.q.time_limit; t.changed = now; await self.push(t)
                    elif t.phase == "PLAYING" and due: ev("timer_expired", team_id=t.id, question_id=t.q.id); await self.next(t, {"ok": False, "timeUp": True})
                    elif t.phase == "RESULT" and due: await self.next(t)       # legacy saved state only
                    elif t.phase == "PLAYING" and all(k in t.values for k in "XYZ") and tuple(t.values[k] for k in "XYZ") != t.checked and now - t.changed >= cfg["lockSeconds"]: await self.check_answer(t)
                    elif t.phase in ("COUNTDOWN", "PLAYING") and rem != t.last_rem: t.last_rem = rem; await self.push(t, save=False)
                except Exception: log.exception("tick failed for %s", t.id)

engine = Engine()
