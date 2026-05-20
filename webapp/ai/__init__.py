from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph


def model(model_id: str, api_key: str, base_url: str) -> BaseChatModel:
    return init_chat_model(
        model=model_id,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
    )


def agent(user_id: str, session_id: str, model: BaseChatModel) -> CompiledStateGraph:
    return create_agent(model=model, user_id=user_id, session_id=session_id)
