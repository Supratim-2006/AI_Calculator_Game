import copy, os
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")  # development | test | live
QUESTION_LOG = os.environ.get("QUESTION_LOG", "data/questions.jsonl")
DEFAULTS = {
    "sequence": [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
    "time": {1: 30, 2: 25, 3: 20, 4: 15, 5: 10},
    "base": {1: 100, 2: 150, 3: 250, 4: 400, 5: 600},
    "sol": {1: [10, 999], 2: [5, 20], 3: [2, 8], 4: [1, 5], 5: [1, 3]},
    "minTarget": 3, "maxTarget": 200, "penalty": 25, "speed": 2,
    "attempts": 3, "countdown": 3, "resultDelay": 4, "minConf": 0.85,
}
cfg = copy.deepcopy(DEFAULTS)
if ENVIRONMENT == "development":  # accelerated timers for developers
    cfg["countdown"], cfg["resultDelay"] = 1, 2
