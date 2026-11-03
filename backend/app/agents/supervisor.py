"""Multi-agent: a supervisor that hands work to specialist agents, then writes the answer.

One agent with every tool works until the tool list grows: the model picks wrong tools more often,
the prompt gets long, and one set of instructions has to fit every job. Splitting the work helps:

    supervisor  reads the question and the reports so far, decides who works next (or that it's done)
    researcher  the knowledge-base tools: search, list and read documents
    analyst     the SQL tools: counts, sums and lookups in the database (only when SQL_AGENT_URL is set)
    writer      the final answer, from the specialists' reports

          START -> supervisor --(researcher | analyst)--> specialist --> supervisor --> ...
                              `--(finish)--> writer --> END

Each specialist is the same agent graph as /agent/chat (loop.build), with fewer tools and its own
instructions, so everything it does (guardrails on tool results, metrics, sources) works the same.
The supervisor decides with structured output (a schema, not free text), so its choice is always
one of the valid names. MAX_HANDOFFS bounds the loop: a confused supervisor must not ping-pong forever.

Trade-off: every hand-off is extra model calls, so a team is slower and dearer than one agent. Worth
it when the jobs really differ (documents vs. data vs. actions), not for "search and answer".
Tools that need approval (AGENT_APPROVAL_TOOLS) are left out of the team: it has no pause/resume.
"""

import operator
from collections.abc import AsyncIterator
from typing import Annotated, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agents import loop
from app.clients.llm import agent_llm, utility_llm
from app.core.config import AGENT_APPROVAL_TOOLS, AGENT_MODEL
from app.guardrails import injection
from app.rag.answer import cite
from app.services import usage

MAX_HANDOFFS = 4
KB_TOOLS = {"search_documents", "list_documents", "read_document"}
SQL_TOOLS = {"describe_database", "query_database"}

ROLES = {
    "researcher": "You find information in the company's documents. Use the tools, then report the facts you "
                  "found with their file names. Report only; don't write the final answer.",
    "analyst": "You answer questions about structured data with SQL. Look at the tables first, then query. "
               "Report the numbers and how you got them. Report only; don't write the final answer.",
}
SUPERVISOR = (
    "You lead a team: {team}. Given the user's question and the reports so far, choose who works next and "
    "what exactly they should find out, or 'finish' when the reports are enough to answer."
)
WRITER = "Write the final answer to the user's question from the team's reports. Cite file names. Be concise."


class Route(BaseModel):
    next: Literal["researcher", "analyst", "finish"]
    instruction: str = Field(default="", description="the specialist's task, specific and self-contained")


class TeamState(TypedDict):
    question: str
    reports: Annotated[list[str], operator.add]
    documents: Annotated[list[Document], operator.add]
    route: Route | None
    handoffs: int


def build(tools: list[BaseTool], tally: usage.Tally):
    usable = [t for t in tools if t.name not in AGENT_APPROVAL_TOOLS]
    team = {name: [t for t in usable if t.name in names]
            for name, names in (("researcher", KB_TOOLS), ("analyst", SQL_TOOLS))}
    team = {name: ts for name, ts in team.items() if ts}  # no SQL configured: no analyst
    workers = {name: loop.build(ts, tally) for name, ts in team.items()}

    async def supervisor(state: TeamState) -> dict:
        if state["handoffs"] >= MAX_HANDOFFS:
            return {"route": Route(next="finish")}
        reports = "\n\n".join(state["reports"]) or "(none yet)"
        route = await utility_llm.with_structured_output(Route).ainvoke([
            SystemMessage(SUPERVISOR.format(team=", ".join(team))),
            HumanMessage(f"Question: {state['question']}\n\nReports so far:\n{reports}"),
        ])
        if route.next != "finish" and route.next not in team:
            route = Route(next="finish")  # chose a specialist we don't have
        get_stream_writer()({"type": "handoff", "agent": route.next, "instruction": route.instruction})
        return {"route": route, "handoffs": state["handoffs"] + 1}

    def specialist(name: str):
        async def work(state: TeamState) -> dict:
            task = state["route"].instruction or state["question"]
            result = await workers[name].ainvoke({
                "messages": [SystemMessage(injection.with_canary(ROLES[name])), HumanMessage(task)],
                "steps": 0, "documents": [], "declined": [],
            })
            report = result["messages"][-1].content if result["messages"] else ""
            return {"reports": [f"[{name}] {task}\n{report}"], "documents": result["documents"]}
        return work

    async def writer(state: TeamState) -> dict:
        write = get_stream_writer()
        reply = None
        async for chunk in agent_llm.astream([
            SystemMessage(injection.with_canary(WRITER)),
            HumanMessage(f"Question: {state['question']}\n\nReports:\n" + "\n\n".join(state["reports"])),
        ]):
            reply = chunk if reply is None else reply + chunk
            if chunk.content:
                write({"type": "token", "text": chunk.content})
        tally.add(reply)
        return {}

    graph = StateGraph(TeamState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("writer", writer)
    for name in team:
        graph.add_node(name, specialist(name))
        graph.add_edge(name, "supervisor")
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", lambda s: "writer" if s["route"].next == "finish" else s["route"].next,
                                ["writer", *team])
    graph.add_edge("writer", END)
    return graph.compile()


async def run(question: str, tools: list[BaseTool]) -> AsyncIterator[dict]:
    """Same event stream as loop.run, plus {"type": "handoff", "agent", "instruction"} per hand-off."""
    tally = usage.Tally()
    state = None
    initial: TeamState = {"question": question, "reports": [], "documents": [], "route": None, "handoffs": 0}
    async for mode, data in build(tools, tally).astream(initial, {"recursion_limit": 4 * MAX_HANDOFFS + 4},
                                                         stream_mode=["custom", "values"]):
        if mode == "custom":
            yield data
        else:
            state = data
    await usage.record("agent", tally, AGENT_MODEL)
    yield {"type": "sources", "sources": cite(state["documents"] if state else [])}
