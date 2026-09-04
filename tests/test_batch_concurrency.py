"""Phase 7 batch-throughput prerequisites (no Blender, no network).

Two cold-path gaps found while preparing the concurrent batch:

1. Every Blender subprocess grabbed every core — N concurrent runs
   oversubscribe a 32-thread machine and corrupt wall-clock measurements.
   The runner now caps threads (explicit argument or THREED_BLENDER_THREADS).
2. The text provider had no deliberate retry posture: the SDK default (2)
   is thin when several agent loops hit one endpoint concurrently. The
   client's max_retries is now config-driven (default 4).
"""

import json

import pytest

from src.blender.runner import (SENTINEL_BEGIN, SENTINEL_END,
                                BlenderRunner)


class _FakeProcess:
    def __init__(self, payload):
        self.returncode = 0
        self.stdout = SENTINEL_BEGIN + json.dumps(payload) + SENTINEL_END
        self.stderr = ""


def _run_op_with_captured_cmd(monkeypatch, threads_env=None, threads_arg=None):
    """Execute a trivial op through a fake Blender install; return the
    command line the runner would have launched."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess({"success": True, "result": {}})

    monkeypatch.setattr("src.blender.runner.subprocess.run", fake_run)
    monkeypatch.setattr("src.blender.runner.locate_blender", lambda: None)
    if threads_env is None:
        monkeypatch.delenv("THREED_BLENDER_THREADS", raising=False)
    else:
        monkeypatch.setenv("THREED_BLENDER_THREADS", threads_env)
    runner = BlenderRunner(blender_path="C:/fake/blender.exe", threads=threads_arg)
    runner.execute_op("topology_report", {"path": "x.glb"})
    return captured["cmd"]


def test_thread_env_var_reaches_the_blender_command(monkeypatch):
    cmd = _run_op_with_captured_cmd(monkeypatch, threads_env="7")
    assert "--threads" in cmd
    assert cmd[cmd.index("--threads") + 1] == "7"


def test_no_thread_flag_without_configuration(monkeypatch):
    cmd = _run_op_with_captured_cmd(monkeypatch, threads_env=None)
    assert "--threads" not in cmd


def test_explicit_thread_argument_wins_over_env(monkeypatch):
    cmd = _run_op_with_captured_cmd(monkeypatch, threads_env="7", threads_arg=3)
    assert cmd[cmd.index("--threads") + 1] == "3"


def test_garbage_thread_env_is_ignored_not_fatal(monkeypatch):
    cmd = _run_op_with_captured_cmd(monkeypatch, threads_env="all")
    assert "--threads" not in cmd


def test_thread_flag_precedes_the_python_script(monkeypatch):
    """Blender parses --threads as a global flag; it must appear before
    --python on the command line."""
    cmd = _run_op_with_captured_cmd(monkeypatch, threads_env="6")
    assert cmd.index("--threads") < cmd.index("--python")


class TestProviderRetries:
    def test_default_max_retries_is_four(self, monkeypatch):
        monkeypatch.delenv("APTOS_BASE_URL", raising=False)
        monkeypatch.delenv("APTOS_MODEL_ID", raising=False)
        from src.ai.aptos import AptosGLMProvider

        provider = AptosGLMProvider(config={"base_url": "http://x/v1"})
        assert provider.max_retries == 4
        assert provider.client.max_retries == 4

    def test_config_override_is_respected(self, monkeypatch):
        monkeypatch.delenv("APTOS_BASE_URL", raising=False)
        monkeypatch.delenv("APTOS_MODEL_ID", raising=False)
        from src.ai.aptos import AptosGLMProvider

        provider = AptosGLMProvider(config={"base_url": "http://x/v1", "max_retries": 7})
        assert provider.max_retries == 7
        assert provider.client.max_retries == 7

    def test_config_file_pins_the_default(self):
        from src.ai.aptos import load_ai_config

        cfg = load_ai_config()
        assert cfg.get("max_retries") == 4, (
            "config/ai.yaml must pin max_retries so the batch retry posture "
            "cannot drift silently"
        )


def test_batch_runner_ignores_thread_cap_of_zero(monkeypatch):
    """threads=0 would mean 'auto' to Blender but 'unset' to us — refuse it
    so the cap is never accidentally the whole machine."""
    cmd = _run_op_with_captured_cmd(monkeypatch, threads_env="0")
    assert "--threads" not in cmd
