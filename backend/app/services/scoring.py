def correct_points(level: int, remaining: int, cfg) -> tuple[int, int]:
    bonus = remaining * cfg["speed"]
    return cfg["base"][level] + bonus, bonus
