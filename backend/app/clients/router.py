"""Model routing: send each question to the cheapest model that can answer it well.

Most questions to a company chatbot are lookups ("how many leave days?"): a small model with the right
chunk answers them fine, fast and almost free. A few need reasoning ("compare the 2025 and 2026 travel
policies and tell me what changed for contractors"): those deserve a larger model. Sending everything
to the large model wastes money and GPU time; sending everything to the small one gives weak answers
exactly where it matters. A router decides per request:

    LLM_ROUTER=heuristic   rules: long questions, comparison/reasoning words, several questions at once,
                           or a lot of retrieved context -> large. Free and instant, but blunt.
    LLM_ROUTER=llm         the small model classifies the question as "simple" or "complex" first. Better
                           judgement, one extra (short) call. Production routers are often a small trained
                           classifier instead: cheaper than an LLM call, tuned on your own traffic.

The large model is ROUTER_LARGE_MODEL: served by vLLM when VLLM_URL is set (deploy/k8s/gpu), else by
Ollama. Measure before and after (evals/compare.py, and the cost per answer in Grafana): routing pays
off only if the small model really is good enough for the easy share.
"""

import re
from functools import cache

from langchain_core.messages import HumanMessage, SystemMessage

from app.clients.llm import llm, utility_llm
from app.core.config import LLM_ROUTER, OLLAMA_URL, ROUTER_LARGE_MODEL, VLLM_URL

HARD_WORDS = re.compile(
    r"(?i)\b(compare|comparison|difference|differences|why|explain|analy[sz]e|trade-?offs?|pros and cons|"
    r"step by step|plan|strategy|evaluate|recommend|summari[sz]e (all|every)|what changed)\b"
)
CLASSIFY = ("Classify the user's question. Answer 'complex' if it needs reasoning, comparing several "
            "things, multi-step analysis or a long structured answer; otherwise 'simple'. One word.")


def is_enabled() -> bool:
    return LLM_ROUTER != "off" and bool(ROUTER_LARGE_MODEL)


@cache
def large_model():
    if VLLM_URL:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise RuntimeError("VLLM_URL needs: uv add langchain-openai (vLLM speaks the OpenAI API)") from e
        # vLLM ignores the key, but the client insists on one
        return ChatOpenAI(model=ROUTER_LARGE_MODEL, base_url=VLLM_URL, api_key="not-needed", temperature=0.2,
                          stream_usage=True)
    from langchain_ollama import ChatOllama
    return ChatOllama(model=ROUTER_LARGE_MODEL, base_url=OLLAMA_URL, temperature=0.2)


def _heuristic(question: str, context_chars: int) -> bool:
    return (
        len(question) > 300
        or bool(HARD_WORDS.search(question))
        or question.count("?") > 1
        or context_chars > 6000  # lots of material to combine
    )


async def _llm_says_complex(question: str) -> bool:
    out = await utility_llm.ainvoke([SystemMessage(CLASSIFY), HumanMessage(question[:1000])])
    return "complex" in (out.content or "").lower()


async def pick(question: str, context_chars: int = 0) -> tuple[object, str]:
    """(model to answer with, "small" | "large"). Never fails: on a router error, the default model."""
    if not is_enabled():
        return llm, "small"
    try:
        hard = _heuristic(question, context_chars) if LLM_ROUTER == "heuristic" else await _llm_says_complex(question)
    except Exception:
        hard = False
    return (large_model(), "large") if hard else (llm, "small")
