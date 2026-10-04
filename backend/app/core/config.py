import copy, os
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")  # development | test | live
QUESTION_LOG = os.environ.get("QUESTION_LOG", "data/questions.jsonl")
DEFAULTS = {
    "sequence": [1] * 6 + [2] * 6 + [3] * 8,             # 20 questions: 6 easy, 6 medium, 8 hard
    "time": {1: 40, 2: 60, 3: 80},                       # seconds: easy 40, medium 60, hard 80
    "base": {1: 100, 2: 100, 3: 100},                    # constant points per question
    "sol": {1: [10, 999], 2: [2, 30], 3: [1, 8]},        # allowed number of valid (X,Y,Z) solutions per level
    "minTarget": 3, "maxTarget": 200, "penalty": 0, "speed": 2,   # no negative marking; bonus = seconds left x speed
    "countdown": 3, "minConf": 0.75, "lockSeconds": 1.0,
}
cfg = copy.deepcopy(DEFAULTS)
if ENVIRONMENT == "development":  # accelerated timers for developers
    cfg["countdown"] = 1
