"""
A Pydantic AI agent that can look glossary terms up itself, via the glossary
MCP server as a tool.

Needs the `examples` dependency group. Instal with `uv sync --group examples --inexact`,
or `uv add "pydantic-ai-slim[mcp]"` directly. Also needs an API key for whichever model
you point `Agent(...)` at - Anthropic's, by default here (`ANTHROPIC_API_KEY` in your environment).

Two ways to wire the glossary server in are shown below:

- `build_subprocess_agent`: launches `slb mcp serve` as a real subprocess,
  the way an external agent (Claude Desktop, Claude Code) would connect to
  it. Works even if the agent and the glossary server are different
  processes, machines, or codebases entirely.

- `build_inprocess_agent`: skips the subprocess and network hop entirely by
  handing the agent the `FastMCP` server object directly. Only possible
  when the agent and the glossary server are in the same Python process,
  but faster and simpler when they are.

Run with `python -m examples.agent`.
"""

import asyncio

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent  # type: ignore[import]
from pydantic_ai.mcp import MCPToolset  # type: ignore[import]

import slb_glossary.mcp as slb_mcp

MODEL = "anthropic:claude-sonnet-4-5"

QUESTIONS = [
    "What does porosity mean in petroleum engineering?",
    "Now compare that with permeability. How are the two different?",
]

SYSTEM_PROMPT = (
    "You are a petroleum engineering assistant. When a question is about "
    "the meaning of an oilfield or energy term, use the glossary tools "
    "available to you rather than answering from memory."
)


def build_subprocess_agent() -> Agent:
    """An agent that talks to `slb mcp serve` as a subprocess, over stdio."""
    toolset = MCPToolset(
        StdioTransport(
            command="slb",
            args=["mcp", "serve", "--tools", "read_only"],
        )
    ).prefixed("glossary")
    return Agent(MODEL, toolsets=[toolset], system_prompt=SYSTEM_PROMPT)


def build_inprocess_agent() -> Agent:
    """
    An agent that talks to an in-process `MCPApp`, no subprocess needed.

    Only meaningful when the agent and the glossary server live in the
    same process, e.g. inside a larger application that already imports
    `slb_glossary` directly rather than shelling out to the `slb` CLI.
    """
    config = slb_mcp.MCPConfig(
        local=slb_mcp.LocalAccess(allow_write=False),
        tools=slb_mcp.Tool.READ_ONLY,
    )
    app = slb_mcp.MCPApp(config)
    toolset = MCPToolset(app.server()).prefixed("glossary")
    return Agent(MODEL, toolsets=[toolset], system_prompt=SYSTEM_PROMPT)


async def run(agent: Agent, label: str) -> None:
    print(f"\n=== {label} ===")
    # `async with agent` opens the toolset's connection once and reuses it
    # for every run inside the block, instead of a fresh subprocess (or, for
    # the in-process agent, a fresh handshake) per question.
    async with agent:
        for question in QUESTIONS:
            print(f"\n> {question}")
            result = await agent.run(question)
            print(result.output)


async def main() -> None:
    await run(build_inprocess_agent(), "in-process agent")
    await run(build_subprocess_agent(), "subprocess agent (slb mcp serve)")


if __name__ == "__main__":
    asyncio.run(main())
