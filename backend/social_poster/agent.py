import os
import pathlib
import sys
from google.adk.agents import Agent
from google.adk.skills import load_skill_from_dir
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams, StreamableHTTPConnectionParams
from google.adk.tools.skill_toolset import SkillToolset
from mcp import StdioServerParameters
from .tools import generate_image

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "skills"
MCP_SERVER = PROJECT_ROOT / "mcp" / "linkedin_server.py"

BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY", "")

buffer_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://mcp.buffer.com/mcp",
        headers={"Authorization": f"Bearer {BUFFER_API_KEY}"},
    ),
    # Framework-level approval gate: see below, not just an instruction.
    require_confirmation=True,
)

linkedin_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_SERVER)],
            env={
                "DRY_RUN": os.environ.get("DRY_RUN", "true"),
                "LINKEDIN_ACCESS_TOKEN": os.environ.get("LINKEDIN_ACCESS_TOKEN", ""),
            },
        ),
    ),
    tool_filter=["get_profile", "create_post"],
    require_confirmation=True,
)

# POST_VIA overrides the LLM's own routing: "buffer" drops the LinkedIn
# tool entirely, "linkedin" drops Buffer even if a key is set. Blank
# exposes both and lets the agent route by what the user asks for.
POST_VIA = os.environ.get("POST_VIA", "").strip().lower()
use_linkedin = POST_VIA != "buffer"
use_buffer = bool(BUFFER_API_KEY) and POST_VIA != "linkedin"

posting_tools = []
if use_linkedin:
    posting_tools.append(linkedin_toolset)
if use_buffer:
    posting_tools.append(buffer_toolset)

skill_toolset = SkillToolset(
    skills=[
        load_skill_from_dir(SKILLS_DIR / "post-formatter"),
        load_skill_from_dir(SKILLS_DIR / "platform-style"),
        load_skill_from_dir(SKILLS_DIR / "brand-voice"),
        load_skill_from_dir(SKILLS_DIR / "poster-style"),
    ],
)

search_agent = Agent(
    name="web_researcher",
    model="gemini-2.5-flash",
    description="Researches facts, dates, and context on the web.",
    instruction="Research the given query and return a concise summary of relevant facts.",
    tools=[google_search],
)

root_agent = Agent(
    name="social_poster",
    model="gemini-2.5-flash",
    description="Helps the user turn an idea into a polished social media post.",
    instruction="""You help the user turn an idea into a social media post.

Workflow:
1. If the idea needs facts, dates, or context, research it with the
   web_researcher tool first.
2. When drafting, load and follow the relevant skills: post-formatter
   (structure), platform-style (rules for the target platform), and
   brand-voice (tone).
3. If the user asks for an image, load and follow the poster-style skill,
   then call generate_image with a detailed visual description and tell
   them where the file was saved.
4. ALWAYS show the finished draft to the user and ask for their approval
   before any posting action.
5. Publishing routing:
   - Use LinkedIn for an immediate post with an image.
   - Use Buffer for scheduling posts or publishing to other platforms (for an immediate post via Buffer, ask Buffer to publish now rather than schedule).

Posting rules (strict):
- Only post AFTER the user has explicitly approved the exact draft shown to
  them. "Yes", "post it" counts; enthusiasm about the idea does not.
- After posting, share the returned confirmation with the user.""",
    tools=[AgentTool(agent=search_agent), generate_image, skill_toolset, *posting_tools],
)