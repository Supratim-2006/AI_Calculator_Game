# AI Calculator Arena (FastAPI)
    cd backend && pip install -r requirements.txt
    ADMIN_PASS=secret JWT_SECRET=$(openssl rand -hex 24) ENVIRONMENT=live uvicorn app.main:app --port 8000
    pytest            # run from backend/
Players: http://host:8000  ·  Admin: http://host:8000/#admin  ·  Docker: `docker compose up`
Camera needs HTTPS (or localhost) on phones. ENVIRONMENT=development shortens countdown/result delays.

## Gameplay rules
- **Leader** opens the site, chooses *Create team (leader)* and enters team name, name, roll number and phone number. The system generates a **Team ID**.
- **Teammates** choose *Join / log in* and enter the Team ID plus their own name, roll number and phone number (max 3 players per team). A roll number can only belong to one team; logging back in needs the same name, roll number and phone.
- Only the **leader** can press START, and only while **all 3 players are online**. Roles (X/Y/Z) stay fixed for the whole game.
- If any player goes offline during the countdown or a question, the game **pauses and the timer stops**; it resumes with the same time left when that player is back.
- No submit/next buttons: the moment X, Y, Z are right the next question starts at once. A wrong combination is silent and free; keep trying until the timer ends.
- Timers: Easy 40s, Medium 60s, Hard 80s. Easy = `+ - *` only; Medium = one variable repeated + a number constraint; Hard = one variable repeated with parentheses + a compound constraint.
- Laptop: camera pinned top-right. Phone: camera stays in the page flow.

## No saved login
Nothing is remembered in the browser: a refresh, closed tab, back button or **Log out** always returns to the login screen. Players must enter the Team ID (or team name for a new team), name, roll number and phone again; the admin must re-enter the password. All responses are sent with `Cache-Control: no-store`. (Only the hand-calibration thresholds are kept on the device.)

## Database records
`players` table: name, roll_no, phone, is_leader, points (team score), team_id; `teams` table: team name, team code (Team ID), score, state. Databases from older versions are upgraded automatically (new columns are added on startup).

## Admin (`/#admin`)
Live leaderboard with members (name, roll, phone), a **search box** (team name, Team ID, player name, roll number, phone), per-team **Delete** (removes the team, its players and questions from the database and disconnects them), and the settings JSON. Teams are now created by leaders, not by the admin.
