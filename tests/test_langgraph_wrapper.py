"""Tests for axonflow.adapters.langgraph_wrapper module.

Tests wrap_langgraph(), GovernedGraph, _AxonFlowGovernanceCallback, and NodeConfig.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from axonflow import AxonFlow
from axonflow.adapters.langgraph import WorkflowApprovalRequiredError, WorkflowBlockedError
from axonflow.adapters.langgraph_wrapper import (
    _DEFAULT_EXCLUDE,
    _INTERNAL_RUNNABLES,
    GovernedGraph,
    NodeConfig,
    _get_callback_class,
    _import_callback_handler,
    wrap_langgraph,
)
from axonflow.workflow import (
    CreateWorkflowResponse,
    GateDecision,
    StepGateResponse,
    StepType,
    WorkflowSource,
    WorkflowStatus,
)


# Ensure the callback class works even when langchain-core is not installed
# (CI only installs [dev] extras). We mock the import to return a base class
# and reset the module-level cache so it's rebuilt each test.
@pytest.fixture(autouse=True)
def _patch_callback_for_ci(monkeypatch):
    """Provide a fake AsyncCallbackHandler base and reset the class cache."""
    import axonflow.adapters.langgraph_wrapper as _mod

    # Reset cache so _get_callback_class() rebuilds from scratch
    monkeypatch.setattr(_mod, "_CallbackClass", None)

    # If langchain-core IS installed, this is a no-op (real import succeeds).
    # If NOT installed, provide a minimal fake base class.
    try:
        _import_callback_handler()
    except ImportError:
        monkeypatch.setattr(
            _mod,
            "_import_callback_handler",
            lambda: type(
                "FakeAsyncCallbackHandler", (), {"raise_error": False, "run_inline": False}
            ),
        )


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockCompiledGraph:
    """Simulates a compiled LangGraph that fires callbacks on each node."""

    def __init__(
        self,
        nodes: list[str],
        tools: list[str] | None = None,
        error_on_node: str | None = None,
    ):
        self.nodes = nodes
        self.tools = tools or []
        self.error_on_node = error_on_node

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: A002
        callbacks = (config or {}).get("callbacks", [])
        for node in self.nodes:
            if node == self.error_on_node:
                msg = f"Node {node} failed"
                raise RuntimeError(msg)
            run_id = uuid4()
            for cb in callbacks:
                await cb.on_chain_start(
                    {"name": node},
                    input,
                    run_id=run_id,
                    metadata={"langgraph_node": node},
                    tags=[],
                )
            # Simulate tools within the "tools" node
            if node == "tools":
                for tool in self.tools:
                    tool_run_id = uuid4()
                    for cb in callbacks:
                        await cb.on_tool_start(
                            {"name": tool},
                            "",
                            run_id=tool_run_id,
                            parent_run_id=run_id,
                            inputs={"arg": "val"},
                        )
                    for cb in callbacks:
                        await cb.on_tool_end(
                            {"result": f"{tool}_done"},
                            run_id=tool_run_id,
                        )
            output = {f"{node}_output": "done"}
            for cb in callbacks:
                await cb.on_chain_end(output, run_id=run_id)
        return {"final": "result"}

    async def astream(self, input, config=None, **kwargs):  # noqa: A002
        result = await self.ainvoke(input, config=config, **kwargs)
        for k, v in result.items():
            yield {k: v}


def _mock_client(gate_decision: GateDecision = GateDecision.ALLOW) -> MagicMock:
    """Create a mock AxonFlow client with standard responses."""
    c = MagicMock(spec=AxonFlow)
    c.create_workflow = AsyncMock(
        return_value=CreateWorkflowResponse(
            workflow_id="wf-test-001",
            workflow_name="test",
            source=WorkflowSource.LANGGRAPH,
            status=WorkflowStatus.IN_PROGRESS,
            created_at=datetime.now(timezone.utc),
        )
    )
    c.step_gate = AsyncMock(
        return_value=StepGateResponse(
            decision=gate_decision,
            step_id="step-1-test",
            reason="Policy violation" if gate_decision == GateDecision.BLOCK else None,
            policy_ids=["pol-1"] if gate_decision == GateDecision.BLOCK else [],
        )
    )
    c.mark_step_completed = AsyncMock(return_value=None)
    c.complete_workflow = AsyncMock(return_value=None)
    c.abort_workflow = AsyncMock(return_value=None)
    c.fail_workflow = AsyncMock(return_value=None)
    return c


# ---------------------------------------------------------------------------
# TestWrapLanggraph
# ---------------------------------------------------------------------------


class TestWrapLanggraph:
    """Tests for the wrap_langgraph() factory function."""

    def test_returns_governed_graph(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["a"])
        result = wrap_langgraph(graph, client=client, workflow_name="test-wf")
        assert isinstance(result, GovernedGraph)

    def test_default_exclude_nodes(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["a"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")
        assert governed._exclude_nodes == _DEFAULT_EXCLUDE

    def test_custom_exclude_nodes(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["a"])
        governed = wrap_langgraph(
            graph, client=client, workflow_name="test-wf", exclude_nodes={"debug", "log"}
        )
        assert governed._exclude_nodes == frozenset({"debug", "log"})


# ---------------------------------------------------------------------------
# TestGovernedGraphLifecycle
# ---------------------------------------------------------------------------


class TestGovernedGraphLifecycle:
    """Tests for GovernedGraph workflow lifecycle management."""

    @pytest.mark.asyncio
    async def test_ainvoke_creates_and_completes_workflow(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        client.create_workflow.assert_called_once()
        client.complete_workflow.assert_called_once_with("wf-test-001")

    @pytest.mark.asyncio
    async def test_ainvoke_returns_graph_result(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        result = await governed.ainvoke({"query": "hello"})

        assert result == {"final": "result"}

    @pytest.mark.asyncio
    async def test_ainvoke_fails_workflow_on_exception(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan", "execute"], error_on_node="execute")
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        with pytest.raises(RuntimeError, match="Node execute failed"):
            await governed.ainvoke({"query": "hello"})

        client.fail_workflow.assert_called_once()
        call_args = client.fail_workflow.call_args
        assert "wf-test-001" in call_args.args
        assert "Node execute failed" in call_args.args[1]

    @pytest.mark.asyncio
    async def test_ainvoke_aborts_on_governance_block(self):
        client = _mock_client(gate_decision=GateDecision.BLOCK)
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        with pytest.raises(WorkflowBlockedError):
            await governed.ainvoke({"query": "hello"})

        client.abort_workflow.assert_called_once()
        call_args = client.abort_workflow.call_args
        assert call_args.args[0] == "wf-test-001"
        assert "Governance block" in call_args.args[1]

    @pytest.mark.asyncio
    async def test_each_ainvoke_creates_new_workflow(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "first"})
        await governed.ainvoke({"query": "second"})

        assert client.create_workflow.call_count == 2

    @pytest.mark.asyncio
    async def test_metadata_and_trace_id_forwarded(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(
            graph,
            client=client,
            workflow_name="test-wf",
            metadata={"env": "test"},
            trace_id="trace-abc-123",
        )

        await governed.ainvoke({"query": "hello"})

        call_args = client.create_workflow.call_args
        request = call_args.args[0]
        assert request.metadata == {"env": "test"}
        assert request.trace_id == "trace-abc-123"


# ---------------------------------------------------------------------------
# TestNodeGovernance
# ---------------------------------------------------------------------------


class TestNodeGovernance:
    """Tests for per-node governance gate checks."""

    @pytest.mark.asyncio
    async def test_check_gate_called_per_node(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan", "search", "report"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        assert client.step_gate.call_count == 3

    @pytest.mark.asyncio
    async def test_step_completed_called_per_node(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan", "search", "report"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        assert client.mark_step_completed.call_count == 3

    @pytest.mark.asyncio
    async def test_excluded_nodes_skipped(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["__start__", "plan", "__end__"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        # Only "plan" should be governed
        assert client.step_gate.call_count == 1
        call_args = client.step_gate.call_args
        request = call_args.args[2]
        assert request.step_name == "plan"

    @pytest.mark.asyncio
    async def test_custom_exclude_nodes(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["debug", "plan", "report"])
        governed = wrap_langgraph(
            graph,
            client=client,
            workflow_name="test-wf",
            exclude_nodes={"debug"},
        )

        await governed.ainvoke({"query": "hello"})

        # "debug" skipped, "plan" and "report" governed
        assert client.step_gate.call_count == 2
        step_names = [call.args[2].step_name for call in client.step_gate.call_args_list]
        assert "debug" not in step_names
        assert "plan" in step_names
        assert "report" in step_names

    @pytest.mark.asyncio
    async def test_node_config_skip(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan", "debug", "report"])
        governed = wrap_langgraph(
            graph,
            client=client,
            workflow_name="test-wf",
            node_config={"debug": NodeConfig(skip=True)},
        )

        await governed.ainvoke({"query": "hello"})

        assert client.step_gate.call_count == 2
        step_names = [call.args[2].step_name for call in client.step_gate.call_args_list]
        assert "debug" not in step_names

    @pytest.mark.asyncio
    async def test_node_config_step_type(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["generate"])
        governed = wrap_langgraph(
            graph,
            client=client,
            workflow_name="test-wf",
            node_config={"generate": NodeConfig(step_type="llm_call")},
        )

        await governed.ainvoke({"query": "hello"})

        call_args = client.step_gate.call_args
        request = call_args.args[2]
        assert request.step_type == StepType.LLM_CALL

    @pytest.mark.asyncio
    async def test_node_config_model_provider(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["generate"])
        governed = wrap_langgraph(
            graph,
            client=client,
            workflow_name="test-wf",
            node_config={
                "generate": NodeConfig(step_type="llm_call", model="gpt-4", provider="openai")
            },
        )

        await governed.ainvoke({"query": "hello"})

        call_args = client.step_gate.call_args
        request = call_args.args[2]
        assert request.model == "gpt-4"
        assert request.provider == "openai"

    @pytest.mark.asyncio
    async def test_internal_runnables_filtered(self):
        """Nodes named after internal LangChain runnables are not governed."""
        client = _mock_client()
        # ChannelRead is in _INTERNAL_RUNNABLES but we deliver it via metadata
        # To test internal runnable filtering, we need to use serialized name
        # without metadata. Build a custom graph for that.

        class InternalRunnableGraph:
            async def ainvoke(self, input, config=None, **kwargs):  # noqa: A002
                callbacks = (config or {}).get("callbacks", [])
                run_id = uuid4()
                # Fire chain_start with only serialized name (no langgraph_node metadata)
                for cb in callbacks:
                    await cb.on_chain_start(
                        {"name": "ChannelRead"},
                        input,
                        run_id=run_id,
                        metadata={},
                        tags=[],
                    )
                for cb in callbacks:
                    await cb.on_chain_end({"out": "done"}, run_id=run_id)
                return {"final": "result"}

        graph = InternalRunnableGraph()
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        # Internal runnables should not trigger gate checks
        assert client.step_gate.call_count == 0


# ---------------------------------------------------------------------------
# TestToolGovernance
# ---------------------------------------------------------------------------


class TestToolGovernance:
    """Tests for per-tool governance within nodes."""

    @pytest.mark.asyncio
    async def test_tool_gates_called(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["tools"], tools=["web_search", "calculator"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        # 1 node gate + 2 tool gates = 3 total step_gate calls
        assert client.step_gate.call_count == 3

        # Check the tool gate calls specifically (calls at index 1 and 2)
        for i in (1, 2):
            call_args = client.step_gate.call_args_list[i]
            request = call_args.args[2]
            assert request.step_type == StepType.TOOL_CALL
            assert request.tool_context is not None

    @pytest.mark.asyncio
    async def test_tool_completed_called(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["tools"], tools=["web_search", "calculator"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        # 1 node completion + 2 tool completions = 3 total
        assert client.mark_step_completed.call_count == 3

    @pytest.mark.asyncio
    async def test_govern_tools_false_skips(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["tools"], tools=["web_search", "calculator"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf", govern_tools=False)

        await governed.ainvoke({"query": "hello"})

        # Only the "tools" node gate, no per-tool gates
        assert client.step_gate.call_count == 1
        # Only the "tools" node completion, no per-tool completions
        assert client.mark_step_completed.call_count == 1

    @pytest.mark.asyncio
    async def test_tool_output_forwarded(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["tools"], tools=["web_search"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        await governed.ainvoke({"query": "hello"})

        # The tool completion goes through adapter.tool_completed -> step_completed
        # -> client.mark_step_completed with a step_id like "step-N-tools-web_search"
        tool_completion_calls = [
            call
            for call in client.mark_step_completed.call_args_list
            if "web_search" in str(call.args[1])  # step_id arg
        ]
        assert len(tool_completion_calls) == 1
        # Verify the output dict was forwarded
        request = tool_completion_calls[0].args[2]
        assert request.output == {"result": "web_search_done"}


# ---------------------------------------------------------------------------
# TestNodeDetection
# ---------------------------------------------------------------------------


class TestNodeDetection:
    """Tests for the governance callback's _detect_node_name method."""

    def _make_callback(self):
        cls = _get_callback_class()
        adapter = MagicMock()
        return cls(
            adapter,
            node_config={},
            exclude_nodes=_DEFAULT_EXCLUDE,
            govern_tools=True,
        )

    def test_detects_via_metadata(self):
        cb = self._make_callback()
        result = cb._detect_node_name(
            serialized={"name": "something_else"},
            metadata={"langgraph_node": "plan"},
        )
        assert result == "plan"

    def test_detects_via_serialized_name(self):
        cb = self._make_callback()
        result = cb._detect_node_name(
            serialized={"name": "plan"},
            metadata={},
        )
        assert result == "plan"

    def test_ignores_internal_runnables(self):
        cb = self._make_callback()
        for name in _INTERNAL_RUNNABLES:
            result = cb._detect_node_name(
                serialized={"name": name},
                metadata={},
            )
            assert result is None, f"Expected None for internal runnable {name}"

    def test_ignores_underscore_prefix(self):
        cb = self._make_callback()
        result = cb._detect_node_name(
            serialized={"name": "_internal"},
            metadata={},
        )
        assert result is None

    def test_none_when_empty(self):
        cb = self._make_callback()
        result = cb._detect_node_name(
            serialized={},
            metadata=None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# TestCallbackMerging
# ---------------------------------------------------------------------------


class TestCallbackMerging:
    """Tests for GovernedGraph._merge_callbacks and config handling."""

    @pytest.mark.asyncio
    async def test_user_callbacks_preserved(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        existing_cb = MagicMock()
        # Give the existing callback matching async methods so it doesn't error
        existing_cb.on_chain_start = AsyncMock()
        existing_cb.on_chain_end = AsyncMock()

        await governed.ainvoke({"query": "hello"}, config={"callbacks": [existing_cb]})

        # The user's callback should have been invoked alongside the governance one
        assert existing_cb.on_chain_start.call_count >= 1

    @pytest.mark.asyncio
    async def test_none_config_handled(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        # Should not raise when config is None
        result = await governed.ainvoke({"query": "hello"}, config=None)
        assert result == {"final": "result"}

    @pytest.mark.asyncio
    async def test_non_list_callbacks_converted(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        existing_cb = MagicMock()
        existing_cb.on_chain_start = AsyncMock()
        existing_cb.on_chain_end = AsyncMock()

        # Pass callbacks as a tuple instead of a list
        await governed.ainvoke({"query": "hello"}, config={"callbacks": (existing_cb,)})

        assert existing_cb.on_chain_start.call_count >= 1


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error propagation through the governed graph."""

    @pytest.mark.asyncio
    async def test_block_propagates(self):
        client = _mock_client(gate_decision=GateDecision.BLOCK)
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        with pytest.raises(WorkflowBlockedError) as exc_info:
            await governed.ainvoke({"query": "hello"})

        assert exc_info.value.step_id == "step-1-test"
        assert exc_info.value.reason == "Policy violation"
        assert "pol-1" in exc_info.value.policy_ids

    @pytest.mark.asyncio
    async def test_exception_in_graph_propagates(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan", "crash"], error_on_node="crash")
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        with pytest.raises(RuntimeError, match="Node crash failed"):
            await governed.ainvoke({"query": "hello"})

        client.fail_workflow.assert_called_once()
        # complete_workflow should NOT have been called
        client.complete_workflow.assert_not_called()

    @pytest.mark.asyncio
    async def test_approval_required_does_not_abort(self):
        """WorkflowApprovalRequiredError must NOT abort the workflow.

        The workflow should remain in a resumable state so the user
        can approve the step and retry.
        """
        client = _mock_client(gate_decision=GateDecision.REQUIRE_APPROVAL)
        client.step_gate.return_value = StepGateResponse(
            decision=GateDecision.REQUIRE_APPROVAL,
            step_id="step-1-plan",
            reason="Requires manager approval",
            approval_url="https://portal.example.com/approve/step-1",
        )
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        with pytest.raises(WorkflowApprovalRequiredError):
            await governed.ainvoke({"query": "hello"})

        # abort_workflow must NOT be called — workflow should stay resumable
        client.abort_workflow.assert_not_called()
        # complete_workflow also should not be called
        client.complete_workflow.assert_not_called()


# ---------------------------------------------------------------------------
# TestSyncInvoke
# ---------------------------------------------------------------------------


class TestSyncInvoke:
    """Tests for synchronous invoke()."""

    def test_invoke_sync(self):
        client = _mock_client()
        graph = MockCompiledGraph(nodes=["plan"])
        governed = wrap_langgraph(graph, client=client, workflow_name="test-wf")

        result = governed.invoke({"query": "hello"})

        assert result == {"final": "result"}
        client.create_workflow.assert_called_once()
        client.complete_workflow.assert_called_once()
