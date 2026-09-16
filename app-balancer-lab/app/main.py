import os
import socket
import uuid
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Идентификация узла (backend node).
# NODE_ID задаётся через переменные окружения при старте контейнера,
# если не задан — генерируется автоматически. Это позволяет однозначно
# определить, с какого backend-узла вернулся ответ (Ограничение №1).
# ---------------------------------------------------------------------------
NODE_ID = os.getenv("NODE_ID", f"node-{uuid.uuid4().hex[:8]}")
HOSTNAME = socket.gethostname()

# ---------------------------------------------------------------------------
# Подключение к внешнему хранилищу (Redis).
# Хранилище общее для всех нод, поэтому выход одной ноды из строя
# не приводит к потере данных (Ограничение №2 и №4).
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="Balancer Lab App", version="1.0.0")


def node_info() -> dict:
    """Информация, однозначно идентифицирующая текущий backend-узел."""
    return {
        "node_id": NODE_ID,
        "hostname": HOSTNAME,
        "pid": os.getpid(),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/")
async def root():
    """Главная страница: показывает, с какого узла пришёл ответ."""
    return {
        "message": "Hello from backend node",
        **node_info(),
    }


@app.get("/api/whoami")
async def whoami(request: Request):
    """
    Эндпоинт для проверки балансировки: клиент видит, какой узел
    обслужил запрос.
    """
    return {
        **node_info(),
        "client": request.client.host if request.client else None,
        "headers": {
            k: v for k, v in request.headers.items()
            if k.lower() in ("x-forwarded-for", "x-real-ip", "user-agent", "host")
        },
    }


@app.get("/api/counter")
async def counter():
    """
    Демонстрация внешнего хранилища: счётчик запросов хранится в Redis,
    а не в памяти процесса.
    """
    value = await redis_client.incr("global_counter")
    return {**node_info(), "global_counter": value}


@app.post("/api/session")
async def create_session(payload: dict | None = None):
    """
    Создание пользовательской сессии. Сессия хранится в Redis
    (Ограничение №4: не в RAM и не в локальных файлах).
    """
    session_id = uuid.uuid4().hex
    data = {
        "session_id": session_id,
        "node_created": NODE_ID,
        "payload": payload or {},
    }
    await redis_client.setex(f"session:{session_id}", 3600, str(data))
    return {**node_info(), "session": data}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    """
    Чтение сессии. Запрос может прийти на ЛЮБУЮ ноду — благодаря Redis
    данные будут найдены независимо от того, кто создал сессию.
    """
    raw = await redis_client.get(f"session:{session_id}")
    if raw is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {**node_info(), "session_raw": raw}


@app.get("/api/health")
async def health():
    """
    Health-check для балансировщика. Проверяет и доступность Redis.
    """
    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    status_code = 200 if redis_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            **node_info(),
            "status": "ok" if redis_ok else "degraded",
            "redis": redis_ok,
        },
    )
