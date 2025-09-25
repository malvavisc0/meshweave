from agno.agent import Agent
from agno.db.redis import RedisDb
from agno.models.openrouter import OpenRouter


def model(model_id: str, api_key: str) -> OpenRouter:
    return OpenRouter(id=model_id, api_key=api_key)


def db(redis_url: str) -> RedisDb:
    return RedisDb(db_url=redis_url)


def agent(
    user_id: str,
    session_id: str,
    db: RedisDb,
    model: OpenRouter,
    markdown: bool = True,
    stream: bool = True,
):
    return Agent(
        user_id=user_id,
        session_id=session_id,
        model=model,
        db=db,
        enable_user_memories=True,
        markdown=markdown,
        stream=stream,
    )
