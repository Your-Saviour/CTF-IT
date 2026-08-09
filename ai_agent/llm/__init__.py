from ai_agent.llm.client import LLMClient

_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


async def close_llm():
    global _llm
    if _llm is not None:
        await _llm.close()
        _llm = None
