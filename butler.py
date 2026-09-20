"""Butler system prompt (hardcoded, per spec) and Claude conversation calls."""
from typing import Optional

import anthropic

from config import Config

_client: Optional[anthropic.Anthropic] = None

MAX_HISTORY_MESSAGES = 20

BUTLER_SYSTEM_PROMPT_TEMPLATE = (
    "You are the Marquis butler. A guide with the mindset of someone who has already "
    "built something significant. You never say that. The quality of your thinking reveals it.\n\n"
    "You give ideas, suggestions and recommendations freely. The one thing you never do: "
    "question the fundamental business vision or suggest pivoting the core idea.\n\n"
    "You never say: Great question, I'd be happy to help, Let's get started, That sounds exciting.\n\n"
    "You say: Three things require your attention. This approach will cost you six weeks. "
    "You've asked this before. The answer hasn't changed.\n\n"
    "No spoon feeding. Show the path. They walk it. Warm but never performatively warm. "
    "Speak in complete paragraphs. Never bullet points. Never more than 3 paragraphs per response.\n\n"
    "AGENT CONTEXT: {agent_context}"
)


def get_client() -> anthropic.Anthropic:
    """Return a lazily-initialized singleton Anthropic client."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _client


def build_agent_context(performance: dict, pathway: dict, blocker: dict, enhancement: dict) -> dict:
    """Collapse each agent's output into one string, per the /conversation response shape."""
    performance_text = performance.get("key_insight") or "No notable pattern yet."

    pathway_text = pathway.get("recommendation") or f"Pathway is {pathway.get('pathway_status', 'on_track')}."

    if blocker.get("stuck_type") not in (None, "none"):
        blocker_text = blocker.get("likely_cause") or f"Possible {blocker['stuck_type']} stuck signal."
    else:
        blocker_text = "No blocker signals detected."

    if enhancement.get("surface_now"):
        enhancement_text = f"{enhancement['surface_now']} — {enhancement.get('reason', '')}"
    else:
        enhancement_text = "Nothing new to surface this session."

    return {
        "performance": performance_text,
        "pathway": pathway_text,
        "blocker": blocker_text,
        "enhancement": enhancement_text,
    }


def _format_agent_context_block(agent_context: dict, prompt_improvements: list) -> str:
    """Render the agent_context dict (+ any approved prompt_improvements) as one string.

    This is exactly what fills {agent_context} in the hardcoded system
    prompt — kept as a single substitution so the rest of the prompt text
    stays byte-for-byte what was specified ("hardcoded — paste exactly").
    """
    lines = [
        f"- {label.capitalize()}: {agent_context[label]}"
        for label in ("performance", "pathway", "blocker", "enhancement")
    ]
    if prompt_improvements:
        lines.append("Approved behavior improvements to apply:")
        lines.extend(f"- {row['suggestion']} ({row.get('reason', '')})" for row in prompt_improvements)
    return "\n".join(lines)


def build_system_prompt(agent_context: dict, prompt_improvements: list) -> str:
    """Fill the hardcoded butler system prompt's one placeholder: {agent_context}."""
    return BUTLER_SYSTEM_PROMPT_TEMPLATE.format(
        agent_context=_format_agent_context_block(agent_context, prompt_improvements)
    )


def _build_messages(history: Optional[list], message: str) -> list:
    """Build the Claude messages array from prior turns plus the new message."""
    messages = []
    if isinstance(history, list):
        for item in history[-MAX_HISTORY_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            # Claude's API only accepts 'user'/'assistant' — the app's own
            # 'butler' role (used in Supabase) is mapped here.
            claude_role = "assistant" if role == "butler" else role
            if claude_role in ("user", "assistant") and isinstance(content, str) and content:
                messages.append({"role": claude_role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def get_butler_response(message: str, system_prompt: str, history: Optional[list] = None) -> str:
    """Call Claude with the butler system prompt and return the full reply text (no streaming)."""
    response = get_client().messages.create(
        model=Config.CLAUDE_MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=_build_messages(history, message),
    )
    return response.content[0].text.strip()
