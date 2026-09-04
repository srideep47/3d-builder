"""The agent surface must not expose arbitrary code execution (Phase 3.0).

`execute_blender_script` let the brain run raw Blender Python, destroying
the validated-spec boundary — the single biggest advantage this system has
over Blender-MCP setups. *3DCodeBench* (arXiv 2606.01057) measured the two
dominant failure modes when vision models author procedural 3D code:
API mismatches and disconnected/floating geometry. A validated ObjectSpec
makes both structurally impossible; an arbitrary-script tool throws that
away.

These tests pin the removal: the tool must stay out of the schema, out of
the executor, and the harness `run_script` op must not be reachable
through the agent executor.
"""

from src.agent.tools import AGENT_TOOLS_SCHEMA, AgentToolExecutor


def test_no_arbitrary_script_tool_in_schema():
    names = [t["function"]["name"] for t in AGENT_TOOLS_SCHEMA]
    assert "execute_blender_script" not in names
    # no tool accepts raw code of any kind
    for tool in AGENT_TOOLS_SCHEMA:
        for prop in tool["function"]["parameters"].get("properties", {}).values():
            assert "code" not in prop, f"{tool['function']['name']} takes code"


def test_executor_refuses_the_removed_tool(tmp_path):
    class _RecordingRunner:
        def __init__(self):
            self.ops = []

        def execute_op(self, op, params=None, timeout_sec=None):
            self.ops.append(op)
            return {"success": True}

    rec = _RecordingRunner()
    executor = AgentToolExecutor(runner=rec, workdir=tmp_path)
    result = executor.execute("execute_blender_script", {"code": "import bpy"})
    assert result["success"] is False
    assert "Unknown tool" in result["error"]
    # nothing reached Blender — the refusal is at the registry, not in a
    # subprocess
    assert rec.ops == []
