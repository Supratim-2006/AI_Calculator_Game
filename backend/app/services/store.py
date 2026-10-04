"""PostgreSQL persistence (SQLite fallback for local dev). Engine state is saved by a background flusher and reloaded on startup."""
import asyncio, logging, os
from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = logging.getLogger("arena")
url, args = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///data/arena.db"), {}
for pre in ("postgres://", "postgresql://"):
    if url.startswith(pre): url = "postgresql+asyncpg://" + url[len(pre):]
if "sslmode=" in url: url, args = url.split("?")[0], {"ssl": True}      # asyncpg takes ssl as an argument
if url.startswith("sqlite"): os.makedirs("data", exist_ok=True)
engine = create_async_engine(url, connect_args=args, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass
class TeamRow(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(60)); code: Mapped[str] = mapped_column(String(16), unique=True)
    score: Mapped[int] = mapped_column(Integer, default=0); state: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
class PlayerRow(Base):
    __tablename__ = "players"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    name: Mapped[str] = mapped_column(String(40)); seat: Mapped[int] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
class QuestionRow(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True); n: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer); expression: Mapped[str] = mapped_column(String(60))
    target: Mapped[int] = mapped_column(Integer); constraint_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer); solution_count: Mapped[int] = mapped_column(Integer)
    time_limit: Mapped[int] = mapped_column(Integer); qhash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

async def init():
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)

async def save(t):
    now, q = datetime.now(timezone.utc), t.q
    st = {"phase": t.phase, "n": t.n, "attempts": t.attempts, "qpts": t.qpts, "roles": t.roles, "values": t.values, "res": t.res, "log": t.log,
          "qid": q.id if q else None, "rem": t.remaining() if t.phase in ("COUNTDOWN", "PLAYING", "RESULT") else 0}
    async with Session() as s:
        await s.merge(TeamRow(id=t.id, name=t.name, code=t.code, score=t.score, state=st, updated_at=now)); await s.flush()
        for i, p in enumerate(t.players):                                   # team members
            if not await s.get(PlayerRow, p.id): s.add(PlayerRow(id=p.id, team_id=t.id, name=p.name, seat=i, joined_at=now))
        if q: await s.merge(QuestionRow(id=q.id, team_id=t.id, n=t.n, seed=q.seed, expression=q.expr, target=q.target, constraint_text=q.con,
                                        difficulty=q.level, solution_count=len(q.solutions), time_limit=q.time_limit, qhash=q.hash, created_at=now))
        await s.commit()

async def load_into(E):
    from .game_engine import Team, Player
    from .question_generator import Question
    import time
    async with Session() as s:
        qs = (await s.scalars(select(QuestionRow))).all(); pls = (await s.scalars(select(PlayerRow).order_by(PlayerRow.seat))).all()
        for r in (await s.scalars(select(TeamRow))).all():
            st, t = r.state or {}, Team(r.id, r.name, r.code)
            t.players = [Player(p.id, p.name) for p in pls if p.team_id == r.id]
            t.phase, t.n, t.score = st.get("phase", "LOBBY"), st.get("n", 0), r.score
            t.attempts, t.qpts, t.roles, t.values, t.res, t.log = st.get("attempts", 0), st.get("qpts", 0), st.get("roles", {}), st.get("values", {}), st.get("res"), st.get("log", [])
            t.history = {q.qhash for q in qs if q.team_id == r.id}
            q = next((x for x in qs if x.id == st.get("qid")), None)
            if q: t.q = Question(q.id, q.seed, q.expression, q.target, q.constraint_text, q.difficulty, E.gen.find_solutions(q.expression, q.target, q.constraint_text), q.qhash, q.time_limit)
            if st.get("rem"): t.deadline = time.monotonic() + st["rem"]      # downtime does not eat the clock
            E.teams[t.id] = t
        E.gen.used |= {q.qhash for q in qs}
    log.info("restored %d teams from database", len(E.teams))

async def flush(E):
    ids, E.dirty = list(E.dirty), set()
    for i in ids:
        try: await save(E.teams[i])
        except Exception: log.exception("save failed for %s", i); E.dirty.add(i)

async def flusher(E):
    while True: await asyncio.sleep(0.5); await flush(E)
