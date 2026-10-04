# AI Calculator Arena (FastAPI)
    cd backend && pip install -r requirements.txt
    ADMIN_PASS=secret JWT_SECRET=$(openssl rand -hex 24) ENVIRONMENT=live uvicorn app.main:app --port 8000
    pytest            # run from backend/
Players: http://host:8000  ·  Admin: http://host:8000/#admin  ·  Docker: `docker compose up`
Camera needs HTTPS (or localhost) on phones. ENVIRONMENT=development shortens countdown/result delays.
