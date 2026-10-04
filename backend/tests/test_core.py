from app.core.config import cfg
from app.services.question_generator import QuestionGenerator, satisfies
from app.services.scoring import correct_points
from app.services.game_engine import Engine, Team, Player

def test_generated_questions_are_solvable_and_unique():
    g, seen = QuestionGenerator(cfg), set()
    for lvl in range(1, 6):
        for i in range(25):
            q = g.generate_question(f"T{i}", i + 1, lvl, set())
            assert q.solutions and all(satisfies(q, *s) for s in q.solutions)
            assert q.hash not in seen; seen.add(q.hash)

def test_team_history_blocks_repeats():
    g, hist = QuestionGenerator(cfg), set()
    hashes = [g.generate_question("A", i, 2, hist).hash for i in range(10)]
    assert len(set(hashes)) == 10

def test_scoring_example():
    assert correct_points(3, 12, cfg) == (274, 24)

def test_client_view_never_leaks_solutions():
    e = Engine(); t = Team("T1", "x", "C"); [t.players.append(Player(f"P{i}", f"n{i}")) for i in range(3)]
    t.q = e.gen.generate_question("T1", 1, 1, set())
    assert "solutions" not in str(e.view(t, "P0")).lower() and "seed" not in e.view(t, "P0")["question"]
