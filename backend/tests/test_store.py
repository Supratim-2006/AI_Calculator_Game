import asyncio, os, tempfile
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db")
from app.services import store
from app.services.game_engine import Engine, Team, Player

def test_team_members_and_state_survive_restart():
    async def go():
        await store.init(); e = Engine(); t = Team("T1", "Ninjas", "ABC123")
        t.players = [Player("P1", "Alice"), Player("P2", "Bob"), Player("P3", "Cara")]; t.roles = {"X": "P1", "Y": "P2", "Z": "P3"}
        t.score, t.phase, t.n = 150, "ASSIGN", 1; t.q = e.gen.generate_question("T1", 1, 1, t.history); e.teams["T1"] = t
        await store.save(t); e2 = Engine(); await store.load_into(e2); r = e2.teams["T1"]
        assert [p.name for p in r.players] == ["Alice", "Bob", "Cara"] and r.score == 150 and r.q.id == t.q.id and r.q.solutions and r.history
    asyncio.run(go())
