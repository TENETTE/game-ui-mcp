"""Errors that reach the model must say what went wrong and how to recover, never an empty message."""
import pytest

from game_ui_mcp import server as s


def _wrapped(fn):
    s.tool(fn)
    return s.mcp._tool_manager.get_tool(fn.__name__).fn if hasattr(s.mcp, "_tool_manager") else None


def test_errors_carry_tool_name_and_a_recovery_hint():
    def probe_value():
        raise ValueError("width must be > 0")

    def probe_bug():
        return 1 / 0

    def probe_key():
        return {}["radius"]

    for fn, needles in ((probe_value, ["probe_value", "width must be > 0", "call probe_value again"]),
                        (probe_bug, ["unexpected ZeroDivisionError", "likely a game-ui-mcp bug", "Do not repeat"]),
                        (probe_key, ["missing key 'radius'"])):
        wrapper = _wrapped(fn)
        if wrapper is None:
            pytest.skip("this MCP SDK does not expose registered tools")
        with pytest.raises(s.ToolError) as info:
            wrapper()
        for needle in needles:
            assert needle in str(info.value)


def test_missing_image_tells_to_use_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="absolute path"):
        s.view_image(str(tmp_path / "nope.png"))
