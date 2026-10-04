from app.core.config import cfg
from app.services.question_generator import QuestionGenerator, satisfies
from app.services.scoring import correct_points
from app.services.game_engine import Engine, Team, Player

def test_generated_questions_are_solvable_and_unique():
    g, seen = QuestionGenerator(cfg), set()
    for lvl in range(1, 4):
        for i in range(25):
            q = g.generate_question(f"T{i}", i + 1, lvl, set())
            assert q.solutions and all(satisfies(q, *s) for s in q.solutions)
            assert all(0 <= v <= 9 for s in q.solutions for v in s)

def test_team_history_blocks_repeats():
    g, hist = QuestionGenerator(cfg), set()
    hashes = [g.generate_question("A", i, 3, hist).hash for i in range(10)]
    assert len(set(hashes)) == 10

def test_scoring_example():
    assert correct_points(3, 12, cfg) == (124, 24)

def test_timers_and_templates():
    assert cfg["time"] == {1: 40, 2: 60, 3: 80}
    g = QuestionGenerator(cfg)
    for _ in range(30):
        e = g.generate_question("T", 1, 1, set())
        assert e.con is None and "(" not in e.expr and not set(e.expr) - set("XYZ+-*")
        m = g.generate_question("T", 1, 2, set())
        assert m.con and "(" not in m.expr and max(m.expr.count(v) for v in "XYZ") == 2
        h = g.generate_question("T", 1, 3, set())
        assert h.con and "(" in h.expr and max(h.expr.count(v) for v in "XYZ") == 2

def test_client_view_never_leaks_solutions():
    e = Engine(); t = Team("T1", "x", "C"); [t.players.append(Player(f"P{i}", f"n{i}")) for i in range(3)]
    t.q = e.gen.generate_question("T1", 1, 1, set())
    assert "solutions" not in str(e.view(t, "P0")).lower() and "seed" not in e.view(t, "P0")["question"]

def test_pause_freezes_clock_while_a_player_is_offline():
    import time
    class W: pass
    e = Engine(); t = Team("T1", "x", "C"); t.players = [Player(f"P{i}", f"n{i}", f"R{i}", "9000000000") for i in range(3)]; t.leader = "P0"
    for p in t.players: p.ws = W()
    t.q = e.gen.generate_question("T1", 1, 1, set()); t.phase, t.deadline = "PLAYING", time.monotonic() + 30
    assert not e.sync(t) and not t.paused
    t.players[2].ws = None                                   # teammate drops
    assert e.sync(t) and t.paused and t.deadline == 0.0
    r = t.remaining(); time.sleep(1.1); assert t.remaining() == r      # frozen
    t.players[2].ws = W()                                    # teammate returns
    assert e.sync(t) and not t.paused and t.remaining() in (r, r - 1) and t.deadline > time.monotonic()

def test_only_leader_can_start_and_only_when_everyone_is_online():
    import asyncio
    class W:
        async def send_text(self, m): pass
    e = Engine(); t = Team("T1", "x", "C"); t.players = [Player(f"P{i}", f"n{i}") for i in range(3)]; t.roles = {"X": "P0", "Y": "P1", "Z": "P2"}; t.leader = "P0"
    for p in t.players: p.ws = W()
    t.q = e.gen.generate_question("T1", 1, 1, set()); t.phase = "ASSIGN"
    asyncio.run(e.handle(t, t.players[1], {"type": "start"})); assert t.phase == "ASSIGN"      # a member cannot start
    t.players[2].ws = None
    asyncio.run(e.handle(t, t.players[0], {"type": "start"})); assert t.phase == "ASSIGN"      # leader, but a player is offline
    t.players[2].ws = W()
    asyncio.run(e.handle(t, t.players[0], {"type": "start"})); assert t.phase == "COUNTDOWN"
