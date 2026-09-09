# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types


logger = logging.getLogger(__name__)

COORDINATOR_MODEL = "gemini-3.1-pro-preview"
WORKER_MODEL = "gemini-3.7-flash"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


def lookup_invoice(invoice_id: str) -> dict:
    """Looks up a customer invoice by its id.

    Args:
        invoice_id: The invoice identifier, e.g. "INV-1042".

    Returns:
        A dict with the invoice status, amount and due date.
    """
    # ponytail: fixture data, swap for the billing API client when one exists
    return {
        "invoice_id": invoice_id,
        "status": "unpaid",
        "amount_usd": 129.00,
        "due_date": "2026-09-15",
    }


def get_order_status(order_id: str) -> dict:
    """Gets the shipping status of an order.

    Args:
        order_id: The order identifier, e.g. "ORD-8871".

    Returns:
        A dict with the order status, carrier and tracking number.
    """
    # ponytail: fixture data, swap for the fulfilment API client when one exists
    return {
        "order_id": order_id,
        "status": "in_transit",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10123456784",
        "eta": "2026-09-03",
    }


def _worker(
    name: str,
    description: str,
    instruction: str,
    tools: list,
    before_tool_callback: Any = None,
) -> Agent:
    """Builds a specialist sub-agent on the shared worker model.

    Args:
        name: The sub-agent name, also the tool name the coordinator calls.
        description: One line telling the coordinator when to route here.
        instruction: The system instruction for the sub-agent.
        tools: Tools available to the sub-agent, on top of memory preload.
        before_tool_callback: Optional policy guard run before each tool call.

    Returns:
        A configured Agent.
    """
    return Agent(
        name=name,
        description=description,
        model=Gemini(
            model=WORKER_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=instruction,
        tools=[PreloadMemoryTool(), *tools],
        before_tool_callback=before_tool_callback,
    )


# --- Policy: tool access control -------------------------------------------
# Billing tools are owned by BillingSpecialist. Add new billing tools to this
# list and every agent guarded by deny_billing_tools inherits the restriction.
BILLING_TOOLS = [lookup_invoice]
BILLING_TOOL_NAMES = frozenset(t.__name__ for t in BILLING_TOOLS)


def deny_billing_tools(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict | None:
    """Blocks any billing tool call on the agent this is attached to.

    Policy: the shipping agent must never access billing tools. Enforced in code
    rather than in the instruction so neither a prompt injection nor a later
    edit to the agent's tool list can get around it.

    Args:
        tool: The tool the model is trying to call.
        args: The arguments the model supplied.
        tool_context: The ADK tool context for this call.

    Returns:
        A refusal dict to short-circuit the call, or None to allow it.
    """
    if tool.name in BILLING_TOOL_NAMES:
        logger.warning(
            "Policy violation: agent %s attempted billing tool %s",
            tool_context.agent_name,
            tool.name,
        )
        return {
            "error": (
                f"Access denied: {tool.name} is a billing tool and is not "
                "available to this agent. Billing questions belong to "
                "BillingSpecialist."
            )
        }
    return None


BillingSpecialist = _worker(
    "BillingSpecialist",
    "Handles invoices, charges, payment status and billing disputes.",
    "You handle billing questions: invoices, charges and payment status. "
    "Use lookup_invoice to fetch invoice details before answering. "
    "Answer with the facts only; the coordinator handles tone.",
    BILLING_TOOLS,
)

ShippingSpecialist = _worker(
    "ShippingSpecialist",
    "Handles order status, tracking numbers, carriers and delivery estimates.",
    "You handle shipping questions: order status, tracking and delivery estimates. "
    "Use get_order_status to fetch order details before answering. "
    "Answer with the facts only; the coordinator handles tone. "
    "You have no access to billing tools or billing data; if a request is "
    "about invoices, charges or payments, say it must go to billing.",
    [get_order_status],
    before_tool_callback=deny_billing_tools,
)

RefundsSpecialist = _worker(
    "RefundsSpecialist",
    "Handles refund eligibility, refund process and refund timelines.",
    "You handle refund requests: eligibility, process and timelines. "
    "Explain the refund policy and next steps; you cannot issue a refund yourself. "
    "Answer with the facts only; the coordinator handles tone.",
    [],
)


COORDINATOR_INSTRUCTION = """You are the customer support coordinator.

Routing:
1. Read the user's message and decide which specialist owns the intent:
   - BillingSpecialist: invoices, charges, payment status, billing disputes.
   - ShippingSpecialist: order status, tracking, carriers, delivery dates.
   - RefundsSpecialist: refund eligibility, refund process, refund timelines.
2. Call that specialist as a tool, passing the user's request plus any order or
   invoice id you already have. Call more than one only if the message truly
   spans two areas.
3. If the message fits no specialist, answer it yourself; use get_weather or
   get_current_time when they apply.

Memory:
- Past conversations with this user may be supplied in a PAST_CONVERSATIONS block.
  Treat them as known facts about the user - their name, plan, contact preference,
  past orders - and use them when routing and when answering directly.

Synthesis:
- Rewrite the specialist's answer in your own words: warm, plain language,
  two to four sentences, no jargon and no mention of specialists or tools.
- Keep every fact, id, amount and date exactly as the specialist gave it.
  Never invent details the specialist did not return.
- Close with the concrete next step the user should take.
- If a specialist could not answer, say so plainly and offer what you can.
"""

async def persist_turn_to_memory(callback_context: CallbackContext) -> None:
    """Persists the completed turn to Memory Bank.

    Runs after the coordinator finishes, so the whole turn - user message,
    specialist tool calls and the final answer - is written in one go and can be
    recalled by PreloadMemoryTool on later sessions.

    Args:
        callback_context: The ADK callback context for the finished invocation.

    Returns:
        None, so the agent's own output is used unchanged.
    """
    try:
        await callback_context.add_session_to_memory()
    except ValueError:
        # ponytail: no memory service configured (local runs) - not fatal to the turn.
        logger.warning("Memory service unavailable; turn not persisted.")


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=COORDINATOR_MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=COORDINATOR_INSTRUCTION,
    # ponytail: AgentTool, not sub_agents - transfer would let the specialist
    # reply to the user directly and there would be nothing left to synthesize.
    tools=[
        # Memory preload injects into the request of the agent it is attached to,
        # so the coordinator needs its own: it routes, and answers anything that
        # fits no specialist, both of which need the user's history.
        PreloadMemoryTool(),
        get_weather,
        get_current_time,
        AgentTool(agent=BillingSpecialist),
        AgentTool(agent=ShippingSpecialist),
        AgentTool(agent=RefundsSpecialist),
    ],
    after_agent_callback=persist_turn_to_memory,
)

app = App(
    root_agent=root_agent,
    name="app",
)
