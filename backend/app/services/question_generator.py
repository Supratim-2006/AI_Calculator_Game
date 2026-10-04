"""Server-side question engine: template -> candidate -> brute-force solve -> filters -> uniqueness."""
import hashlib, itertools, json, os, random, secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache

LO, HI = 0, 9   # every variable is 0..9
TEMPLATES = {
    # 1 = Easy: +, - and * only, every variable used once, no constraints
    1: ["X+Y+Z", "X+Y-Z", "X-Y+Z", "X*Y+Z", "X+Y*Z", "X*Y-Z", "X*Z+Y", "Y*Z+X", "X*Z-Y", "Y*Z-X"],
    # 2 = Medium: exactly one variable repeated, no parentheses, one simple number constraint
    2: ["X*Y+X*Z", "X*Y+Y*Z", "X*Z+Y*Z", "X*Y-X*Z", "X*X+Y*Z", "X*Y+X-Z", "X*Y-X+Z", "Y*Z+Y+X", "X*Z+X+Y"],
    # 3 = Hard: exactly one variable repeated, with parentheses, compound number constraint
    3: ["X*(Y+Z)+X", "(X+Y)*(X+Z)", "(X+Y)*(X-Z)", "(X+Y)*Z-X", "(X+Z)*Y+X", "X*(Y-Z)+Y",
        "(X+Y)*Z+Y", "(X+Y)*(Y+Z)", "(X+Y)*X+Z", "X*(X+Y)-Z", "(X-Y)*(X+Z)"],
}
CONSTRAINTS = {
    # medium
    "X > Y": lambda x, y, z: x > y,
    "Y < Z": lambda x, y, z: y < z,
    "X is even": lambda x, y, z: x % 2 == 0,
    "Z is odd": lambda x, y, z: z % 2 == 1,
    # hard
    "X > Y > Z": lambda x, y, z: x > y > z,
    "X even, Y odd, Z > 2": lambda x, y, z: x % 2 == 0 and y % 2 == 1 and z > 2,
    "Z odd, X > Y": lambda x, y, z: z % 2 == 1 and x > y,
}
CONSTRAINTS_BY_LEVEL = {2: ["X > Y", "Y < Z", "X is even", "Z is odd"],
                        3: ["X > Y > Z", "X even, Y odd, Z > 2", "Z odd, X > Y"]}

@cache
def _code(expr: str):  # expressions only ever come from TEMPLATES above, never from clients
    return compile(expr, "<q>", "eval")

def evaluate(expr, x, y, z):
    return eval(_code(expr), {"__builtins__": {}}, {"X": x, "Y": y, "Z": z})

def satisfies(q, x, y, z):
    return evaluate(q.expr, x, y, z) == q.target and (q.con is None or CONSTRAINTS[q.con](x, y, z))

@dataclass
class Question:
    id: str; seed: int; expr: str; target: int; con: str | None; level: int
    solutions: list; hash: str; time_limit: int
    @property
    def display(self): return self.expr.replace("*", "×")

class QuestionGenerator:
    def __init__(self, cfg, log_path=None):
        self.cfg, self.used, self.log_path = cfg, set(), log_path   # `used` = global uniqueness

    def find_solutions(self, expr, target, con=None):
        f = CONSTRAINTS.get(con)
        return [s for s in itertools.product(range(LO, HI + 1), repeat=3)
                if evaluate(expr, *s) == target and (f is None or f(*s))]

    @staticmethod
    def make_hash(expr, target, con, level):
        return hashlib.sha256(f"{expr}|{target}|{con}|{level}".encode()).hexdigest()

    def generate_question(self, team_id, n, level, history: set) -> Question:
        c = self.cfg
        for i in range(900):
            relax = i // 200                       # widen solution range if the pool runs dry
            seed = secrets.randbelow(2**31 - 1) + 1; r = random.Random(seed)
            expr = r.choice(TEMPLATES[level]); con = r.choice(CONSTRAINTS_BY_LEVEL[level]) if level in CONSTRAINTS_BY_LEVEL else None
            x, y, z = (r.randint(LO, HI) for _ in range(3))
            if len({x, y, z}) == 1 or sum(v > 0 for v in (x, y, z)) < 2 or (con and not CONSTRAINTS[con](x, y, z)): continue
            target = evaluate(expr, x, y, z)
            if not c["minTarget"] <= target <= c["maxTarget"]: continue
            h = self.make_hash(expr, target, con, level)
            if h in history or (i < 300 and h in self.used): continue
            sols = self.find_solutions(expr, target, con)
            lo, hi = c["sol"][level]; lo = max(1, lo // (1 + relax)); hi *= 1 + relax
            if not lo <= len(sols) <= hi: continue
            self.used.add(h); history.add(h)
            q = Question(f"{team_id}-Q{n:03d}", seed, expr, target, con, level, sols, h, c["time"][level])
            self._log(team_id, q); return q
        raise RuntimeError("question generator exhausted")

    def _log(self, team_id, q):
        if not self.log_path: return
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"team": team_id, "question_id": q.id, "seed": q.seed, "expression": q.expr, "target": q.target,
                                "constraint": q.con, "difficulty": q.level, "solutions": len(q.solutions), "time_limit": q.time_limit,
                                "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
