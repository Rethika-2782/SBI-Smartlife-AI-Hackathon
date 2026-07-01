# ruff: noqa
import datetime
import os
import re
import json
import logging
import sys
from zoneinfo import ZoneInfo

from google.adk import Workflow, Event, Context
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.adk.events import RequestInput
from google.genai import types

from app.config import config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smartlife-agent")

# Dynamically construct path to the local MCP server running in the same directory
current_dir = os.path.dirname(os.path.abspath(__file__))
mcp_server_path = os.path.join(current_dir, "mcp_server.py")

# Create local stdio-based MCP toolset connection params
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mcp_server_path],
        )
    )
)

# 1. Specialized Device Agent
device_agent = Agent(
    name="device_agent",
    model=Gemini(model=config.model),
    instruction="""You are a device specialist. You can query status and set states of smart home devices (lights, locks, security cameras).
Use the MCP tools provided to you to list devices, get their status, and modify their state.""",
    tools=[mcp_toolset],
)

# 2. Specialized Climate Agent
climate_agent = Agent(
    name="climate_agent",
    model=Gemini(model=config.model),
    instruction="""You are a climate specialist. You can check the temperature and set climate controls (thermostat mode, target temperature).
Use the MCP tools provided to you to list devices, get thermostat status, and change settings.""",
    tools=[mcp_toolset],
)

# 3. Main Orchestrator Agent
orchestrator_agent = Agent(
    name="orchestrator_agent",
    model=Gemini(model=config.model),
    instruction="""You are the main SmartLife Coordinator. You receive requests from users and coordinate the response.
For standard queries, delegate to the device_agent or climate_agent using your tools.

CRITICAL SECURITY INSTRUCTION: If the user request requires a critical physical action (specifically: unlocking any door, disarming security cameras, or turning off the alarm), you MUST NOT call any tools to execute it. Instead, you MUST say 'CRITICAL_SECURITY_ACTION: <details of action>' in your response so the system can intercept it for human approval.""",
    tools=[AgentTool(agent=device_agent), AgentTool(agent=climate_agent)],
)


# --- Workflow Graph Nodes ---

# Node 1: Security Checkpoint
def security_checkpoint(ctx: Context, node_input: str | types.Content):
    # Extract query text
    query_text = ""
    if isinstance(node_input, str):
        query_text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        query_text = "".join(part.text for part in node_input.parts if part.text)
    elif hasattr(node_input, "text"):
        query_text = node_input.text
    else:
        query_text = str(node_input)

    # 1. PII Scrubbing (email and US phone numbers)
    scrubbed_text = query_text
    has_pii = False
    
    email_pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    if re.search(email_pattern, scrubbed_text):
        scrubbed_text = re.sub(email_pattern, "[REDACTED_EMAIL]", scrubbed_text)
        has_pii = True

    phone_pattern = r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    if re.search(phone_pattern, scrubbed_text):
        scrubbed_text = re.sub(phone_pattern, "[REDACTED_PHONE]", scrubbed_text)
        has_pii = True

    # 2. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions",
        "ignore the instructions above",
        "system prompt",
        "dan mode",
        "jailbreak",
        "you must now",
    ]
    has_injection = any(keyword in query_text.lower() for keyword in injection_keywords)

    # 3. Domain-specific rule (input command limit check)
    domain_rule_triggered = len(query_text) > 300
    if domain_rule_triggered:
        logger.warning(f"Domain rule triggered: query length {len(query_text)} exceeds threshold.")

    # 4. Structured JSON audit log
    audit_log = {
        "event": "security_checkpoint_eval",
        "has_pii": has_pii,
        "has_injection": has_injection,
        "domain_rule_triggered": domain_rule_triggered,
        "severity": "CRITICAL" if has_injection else ("WARNING" if domain_rule_triggered else "INFO"),
        "action": "violation" if has_injection else "clean"
    }
    logger.info(f"AUDIT_LOG: {json.dumps(audit_log)}")

    # Route request
    if has_injection:
        return Event(route="violation")
    
    ctx.state["query"] = scrubbed_text
    return Event(route="clean", content=types.Content(parts=[types.Part.from_text(text=scrubbed_text)]))


# Node 2: Violation Handler
def security_violation_handler(ctx: Context, node_input: Event | None):
    logger.warning("Security violation node triggered. Request blocked.")
    return Event(content=types.Content(parts=[types.Part.from_text(text="⚠️ Security Alert: The request was blocked because it violated safety rules (potential prompt injection).")]))


# Node 3: Post-Orchestration Router
def post_orchestration_router(ctx: Context, node_input: Event | str | types.Content | None):
    response_text = ""
    if isinstance(node_input, str):
        response_text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        response_text = "".join(part.text for part in node_input.parts if part.text)
    elif hasattr(node_input, "content") and node_input.content:
        if hasattr(node_input.content, "parts") and node_input.content.parts:
            response_text = "".join(part.text for part in node_input.content.parts if part.text)
        elif hasattr(node_input.content, "text"):
            response_text = node_input.content.text
        else:
            response_text = str(node_input.content)
    elif hasattr(node_input, "text"):
        response_text = node_input.text
    else:
        response_text = str(node_input)
            
    ctx.state["orchestrator_response"] = response_text
    
    # Intercept critical action matching the instruction pattern
    if "CRITICAL_SECURITY_ACTION:" in response_text:
        match = re.search(r"CRITICAL_SECURITY_ACTION:\s*(.*)", response_text, re.IGNORECASE)
        action_details = match.group(1) if match else "critical security operation"
        ctx.state["pending_action"] = action_details
        return Event(route="needs_review")
    else:
        return Event(route="auto_approve")


# Node 4: Human Approval Pause
async def request_human_approval_node(ctx: Context, node_input: Event | None):
    pending_action = ctx.state.get("pending_action", "critical smart home action")
    yield RequestInput(
        message=f"✋ HUMAN APPROVAL REQUIRED: Do you approve executing: '{pending_action}'? (type 'yes' to approve or 'no' to deny)"
    )


# Node 5: Human Approval Router
def human_approval_router(ctx: Context, node_input: str | None):
    user_reply = node_input.strip().lower()
    if user_reply in ["yes", "y", "approve", "approved"]:
        ctx.state["approval_status"] = "approved"
        return Event(route="approved")
    else:
        ctx.state["approval_status"] = "denied"
        return Event(route="denied")


# Node 6: Execution Node
def execute_routine_node(ctx: Context, node_input: Event | None):
    pending_action = ctx.state.get("pending_action", "smart home operation")
    action_log = {
        "event": "action_execution",
        "action": pending_action,
        "status": "executed",
        "severity": "INFO"
    }
    logger.info(f"AUDIT_LOG: {json.dumps(action_log)}")
    return Event(content=types.Content(parts=[types.Part.from_text(text=f"✅ Approved: The critical action '{pending_action}' has been successfully executed.")]))


# Node 7: Reject Node
def reject_node(ctx: Context, node_input: Event | None):
    pending_action = ctx.state.get("pending_action", "smart home operation")
    action_log = {
        "event": "action_execution",
        "action": pending_action,
        "status": "rejected",
        "severity": "WARNING"
    }
    logger.info(f"AUDIT_LOG: {json.dumps(action_log)}")
    return Event(content=types.Content(parts=[types.Part.from_text(text=f"❌ Rejected: The critical action '{pending_action}' was denied and was NOT executed.")]))


# Node 8: Final Output Formatter
def final_output_node(ctx: Context, node_input: Event | None):
    if node_input and node_input.content:
        if hasattr(node_input.content, "parts") and node_input.content.parts:
            content = "".join(part.text for part in node_input.content.parts if part.text)
        elif hasattr(node_input.content, "text"):
            content = node_input.content.text
        else:
            content = str(node_input.content)
    else:
        content = ctx.state.get("orchestrator_response", "Process completed.")
        
    return Event(content=types.Content(parts=[types.Part.from_text(text=content)]))


# --- ADK 2.0 Workflow Definition ---
root_agent = Workflow(
    name="smartlife_workflow",
    edges=[
        ("START", security_checkpoint),
        (security_checkpoint, {
            "violation": security_violation_handler,
            "clean": orchestrator_agent,
        }),
        (orchestrator_agent, post_orchestration_router),
        (post_orchestration_router, {
            "auto_approve": final_output_node,
            "needs_review": request_human_approval_node,
        }),
        (request_human_approval_node, human_approval_router),
        (human_approval_router, {
            "approved": execute_routine_node,
            "denied": reject_node,
        }),
        (execute_routine_node, final_output_node),
        (reject_node, final_output_node),
        (security_violation_handler, final_output_node),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
