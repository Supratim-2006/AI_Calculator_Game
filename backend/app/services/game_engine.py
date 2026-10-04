"""Authoritative game engine: per-team state machine, server clock, validation, scoring."""
import asyncio, json, logging, math, time
from .question_generator import QuestionGenerator, satisfies, evaluate
from .scoring import correct_points
from ..core.config import cfg, QUESTION_LOG

log = logging.getLogger("arena")
def ev(event, **kw): log.info(json.dumps({"ts": time.time(), "event": event, **kw}))

ALLOWED = {"LOBBY": {"ASSIGN"}, "ASSIGN": {"COUNTDOWN", "RESULT"}, "COUNTDOWN": {"PLAYING", "RESULT"},
           "PLAYING": {"RESULT"}, "RESULT": {"ASSIGN", "FINISHED"}, "FINISHED": set()}
class InvalidTransition(Exception): pass

class Player:
    def __init__(self, pid, name): self.id, self.name, self.ws = pid, name, None
class Team:
    def __init__(self, tid, name, code):
        self.id, self.name, self.code = tid, name, code
        self.players, self.roles, self.values, self.history, self.log = [], {}, {}, set(), []
        self.phase, self.q, self.n, self.score, self.attempts, self.qpts = "LOBBY", None, 0, 0, 0, 0
        self.deadline, self.res, self.last_rem = 0.0, None, -1
    def remaining(self): return max(0, math.ceil(self.deadline - time.monotonic())) if self.deadline else 0
    def role_of(self, pid): return next((r for r, p in self.roles.items() if p == pid), None)

class Engine:
    def __init__(self):
        self.teams: dict[str, Team] = {}
        self.gen = QuestionGenerator(cfg, QUESTION_LOG)
        self.dirty = set()

    def go(self, t, phase):
        if phase not in ALLOWED[t.phase]: raise InvalidTransition(f"{t.phase} -> {phase}")
        t.phase = phase

    async def push(self, t, save=True):
        if save: self.dirty.add(t.id)
        msgs = [(p.ws, json.dumps(self.view(t, p.id))) for p in t.players if p.ws]
        await asyncio.gather(*(w.send_text(m) for w, m in msgs), return_exceptions=True)

    def view(self, t, pid):  # the ONLY payload clients get: no solutions, no seed
        q = t.q
        return {"team_id": t.id, "team": t.name, "phase": t.phase, "score": t.score, "log": t.log, "attempts": t.attempts,
                "max_attempts": cfg["attempts"], "remaining": t.remaining(), "values": t.values, "res": t.res,
                "players": [{"id": p.id, "name": p.name, "online": p.ws is not None, "role": t.role_of(p.id)} for p in t.players],
                "you": {"id": pid, "role": t.role_of(pid)},
                "question": q and {"question_id": q.id, "expression": q.display, "target": q.target,
                                   "difficulty": q.level, "time_limit": q.time_limit, "constraint": q.con}}

    async def next(self, t):
        if t.phase == "FINISHED": raise InvalidTransition("finished")
        if t.phase in ("ASSIGN", "COUNTDOWN", "PLAYING"):  # admin skip
            t.res = {"ok": False, "timeUp": True}; await self.finish(t, False, 0)
        t.n += 1; t.values, t.res, t.attempts, t.qpts = {}, None, 0, 0
        if t.n > len(cfg["sequence"]): self.go(t, "FINISHED"); t.q = None
        else:
            t.q = self.gen.generate_question(t.id, t.n, cfg["sequence"][t.n - 1], t.history); self.go(t, "ASSIGN")
            ev("question_generated", team_id=t.id, question_id=t.q.id, difficulty=t.q.level)
        await self.push(t)

    async def finish(self, t, ok, earned):
        t.log.append({"n": t.n, "pts": t.qpts + earned, "ok": ok}); t.score = max(0, t.score + earned)
        self.go(t, "RESULT"); t.deadline = time.monotonic() + cfg["resultDelay"]; await self.push(t)

    async def handle(self, t, p, m):
        typ, role = m.get("type"), t.role_of(p.id)
        if typ == "role" and t.phase == "ASSIGN" and m.get("role") in ("X", "Y", "Z"):
            r = m["role"]
            if r != role: other = t.roles[r]; t.roles[r] = p.id; t.roles[role] = other   # swap keeps a valid permutation
            await self.push(t)
        elif typ == "start" and t.phase == "ASSIGN" and len(set(t.roles.values())) == 3:
            self.go(t, "COUNTDOWN"); t.deadline = time.monotonic() + cfg["countdown"]; await self.push(t)  # roles locked
        elif typ == "digit" and t.phase == "PLAYING" and role:
            d, c = m.get("digit"), m.get("conf")
            if type(d) is int and 0 <= d <= 9 and isinstance(c, (int, float)) and c >= cfg["minConf"]:
                t.values[role] = d; await self.push(t)
        elif typ == "submit" and t.phase == "PLAYING":
            if time.monotonic() >= t.deadline: return                      # late: tick loop ends the question
            if any(k not in t.values for k in "XYZ"): return
            x, y, z, q = t.values["X"], t.values["Y"], t.values["Z"], t.q
            if satisfies(q, x, y, z):
                pts, bonus = correct_points(q.level, t.remaining(), cfg)
                t.res = {"ok": True, "pts": pts, "bonus": bonus, "X": x, "Y": y, "Z": z}
                ev("answer_correct", team_id=t.id, player_id=p.id, question_id=q.id, pts=pts); await self.finish(t, True, pts)
            else:
                got = evaluate(q.expr, x, y, z); t.attempts += 1; t.qpts -= cfg["penalty"]; t.score = max(0, t.score - cfg["penalty"])
                t.res = {"ok": False, "X": x, "Y": y, "Z": z, "got": got, "required": q.target, "constraintFailed": got == q.target}
                ev("answer_wrong", team_id=t.id, player_id=p.id, question_id=q.id)
                if cfg["attempts"] and t.attempts >= cfg["attempts"]: await self.finish(t, False, 0)
                else: await self.push(t)

    async def run(self):  # server-authoritative clock
        while True:
            await asyncio.sleep(0.25); now = time.monotonic()
            for t in list(self.teams.values()):
                try:
                    due, rem = t.deadline and now >= t.deadline, t.remaining()
                    if t.phase == "COUNTDOWN" and due: self.go(t, "PLAYING"); t.deadline = now + t.q.time_limit; await self.push(t)
                    elif t.phase == "PLAYING" and due:
                        t.res = {"ok": False, "timeUp": True}; ev("timer_expired", team_id=t.id, question_id=t.q.id); await self.finish(t, False, 0)
                    elif t.phase == "RESULT" and due: await self.next(t)
                    elif t.phase in ("COUNTDOWN", "PLAYING") and rem != t.last_rem: t.last_rem = rem; await self.push(t, save=False)
                except Exception: log.exception("tick failed for %s", t.id)

engine = Engine()
