"""1-line wrapper for LangGraph graphs with AxonFlow governance.

Wrap a compiled LangGraph StateGraph with AxonFlow policy enforcement
at every node transition — no changes to the graph definition needed.

Example::

    from axonflow import AxonFlow
    from axonflow.adapters import wrap_langgraph

    graph = create_my_langgraph()
    governed = wrap_langgraph(graph, client=client, workflow_name="my-pipeline")
    result = await governed.ainvoke({"query": "test"})
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from axonflow.adapters.langgraph import (
    AxonFlowLangGraphAdapter,
    WorkflowApprovalRequiredError,
    WorkflowBlockedError,
)
from axonflow.telemetry import register_adapter
from axonflow.workflow import WorkflowSource

if TYPE_CHECKING:
    from axonflow import AxonFlow


def _import_callback_handler() -> type:
    """Import langchain-core AsyncCallbackHandler with a helpful error."""
    try:
        from langchain_core.callbacks import AsyncCallbackHandler
    except ImportError:
        msg = (
            "langchain-core is required for wrap_langgraph(). "
            "Install with: pip install axonflow[langgraph]"
        )
        raise ImportError(msg) from None
    else:
        return AsyncCallbackHandler


@dataclass(frozen=True)
class NodeConfig:
    """Per-node governance configuration.

    Attributes:
        step_type: Override the step type for this node
            (``"llm_call"``, ``"tool_call"``, ``"connector_call"``).
        model: LLM model name to report in the gate request.
        provider: LLM provider to report in the gate request.
        skip: If ``True``, governance is skipped entirely for this node.
    """

    step_type: str | None = None
    model: str | None = None
    provider: str | None = None
    skip: bool = False


_DEFAULT_EXCLUDE: frozenset[str] = frozenset({"__start__", "__end__"})

_INTERNAL_RUNNABLES: frozenset[str] = frozenset(
    {
        "ChannelRead",
        "ChannelWrite",
        "ChannelInvoke",
        "RunnableSequence",
        "RunnableLambda",
        "RunnableParallel",
        "RunnablePassthrough",
        "RunnableAssign",
    }
)


def _make_callback_class() -> type:
    """Build the callback class with proper AsyncCallbackHandler inheritance.

    We create the class dynamically so that ``langchain_core`` is only
    imported when actually needed (lazy import).
    """
    AsyncCallbackHandler = _import_callback_handler()

    class _Callback(AsyncCallbackHandler):  # type: ignore[misc,valid-type]
        """LangChain callback handler that enforces AxonFlow governance.

        Node detection strategy (priority order):

        1. ``metadata["langgraph_node"]`` — documented LangGraph API
        2. ``serialized["name"]`` filtered against internal runnables

        ``raise_error = True`` ensures that a :class:`WorkflowBlockedError`
        raised inside a callback propagates up and halts graph execution.
        """

        raise_error: bool = True

        def __init__(
            self,
            adapter: AxonFlowLangGraphAdapter,
            *,
            node_config: dict[str, NodeConfig],
            exclude_nodes: frozenset[str],
            govern_tools: bool,
        ) -> None:
            super().__init__()
            self._adapter = adapter
            self._node_config = node_config
            self._exclude_nodes = exclude_nodes
            self._govern_tools = govern_tools

            self._lock = threading.Lock()
            self._active_nodes: dict[UUID, str] = {}

        # --------------------------------------------------------------
        # Node detection helpers
        # --------------------------------------------------------------

        def _detect_node_name(
            self,
            serialized: dict[str, Any] | None,
            metadata: dict[str, Any] | None,
        ) -> str | None:
            # Strategy 1: documented LangGraph metadata key
            if metadata and "langgraph_node" in metadata:
                return str(metadata["langgraph_node"])

            # Strategy 2: serialized name, filtering internal runnables
            if serialized:
                name: str = serialized.get("name", "")
                if name and name not in _INTERNAL_RUNNABLES and not name.startswith("_"):
                    return name

            return None

        def _should_govern(self, node_name: str) -> bool:
            if node_name in self._exclude_nodes:
                return False
            nc = self._node_config.get(node_name)
            return not (nc and nc.skip)

        def _get_step_type(self, node_name: str) -> str:
            nc = self._node_config.get(node_name)
            if nc and nc.step_type:
                return nc.step_type
            return "tool_call"

        # --------------------------------------------------------------
        # Chain callbacks (LangGraph node boundaries)
        # --------------------------------------------------------------

        async def on_chain_start(
            self,
            serialized: dict[str, Any],
            inputs: dict[str, Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            node_name = self._detect_node_name(serialized, metadata)
            if not node_name or not self._should_govern(node_name):
                return

            with self._lock:
                self._active_nodes[run_id] = node_name

            nc = self._node_config.get(node_name)
            await self._adapter.check_gate(
                step_name=node_name,
                step_type=self._get_step_type(node_name),
                model=nc.model if nc else None,
                provider=nc.provider if nc else None,
                step_input=inputs if isinstance(inputs, dict) else {},
            )

        async def on_chain_end(
            self,
            outputs: dict[str, Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            **kwargs: Any,
        ) -> None:
            with self._lock:
                node_name = self._active_nodes.pop(run_id, None)
            if not node_name:
                return

            output_dict = outputs if isinstance(outputs, dict) else {}
            await self._adapter.step_completed(step_name=node_name, output=output_dict)

        async def on_chain_error(
            self,
            error: BaseException,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            **kwargs: Any,
        ) -> None:
            with self._lock:
                self._active_nodes.pop(run_id, None)

        # --------------------------------------------------------------
        # Tool callbacks (per-tool governance)
        # --------------------------------------------------------------

        async def on_tool_start(
            self,
            serialized: dict[str, Any],
            input_str: str,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            inputs: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            if not self._govern_tools:
                return
            tool_name = serialized.get("name", "unknown_tool")

            with self._lock:
                self._active_nodes[run_id] = f"tool:{tool_name}"

            await self._adapter.check_tool_gate(
                tool_name=tool_name,
                tool_type="function",
                tool_input=inputs if isinstance(inputs, dict) else {},
            )

        async def on_tool_end(
            self,
            output: Any,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            **kwargs: Any,
        ) -> None:
            if not self._govern_tools:
                return

            with self._lock:
                tracked = self._active_nodes.pop(run_id, None)
            if not tracked or not tracked.startswith("tool:"):
                return

            tool_name = tracked[5:]  # strip "tool:" prefix
            output_dict = {"result": str(output)} if not isinstance(output, dict) else output
            await self._adapter.tool_completed(tool_name=tool_name, output=output_dict)

        async def on_tool_error(
            self,
            error: BaseException,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            **kwargs: Any,
        ) -> None:
            with self._lock:
                self._active_nodes.pop(run_id, None)

        # --------------------------------------------------------------
        # LLM callbacks — governance handled at node level
        # --------------------------------------------------------------

        async def on_llm_start(
            self,
            serialized: dict[str, Any],
            prompts: list[str],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> None:
            pass

        async def on_llm_end(
            self,
            response: Any,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> None:
            pass

    return _Callback


# Module-level cache for the dynamically created callback class
_CallbackClass: type | None = None


def _get_callback_class() -> type:
    """Return the callback class, creating it on first use."""
    global _CallbackClass  # noqa: PLW0603
    if _CallbackClass is None:
        _CallbackClass = _make_callback_class()
    return _CallbackClass


class GovernedGraph:
    """A LangGraph compiled graph wrapped with AxonFlow governance.

    Each call to :meth:`ainvoke` or :meth:`invoke` creates a new AxonFlow
    workflow.  The ``GovernedGraph`` instance is reusable across invocations.

    Do not instantiate directly — use :func:`wrap_langgraph` instead.
    """

    def __init__(
        self,
        graph: Any,
        *,
        client: AxonFlow,
        workflow_name: str,
        source: WorkflowSource = WorkflowSource.LANGGRAPH,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        node_config: dict[str, NodeConfig] | None = None,
        exclude_nodes: frozenset[str] = _DEFAULT_EXCLUDE,
        govern_tools: bool = True,
    ) -> None:

        # Declare this adapter on the next telemetry heartbeat. Without it, an
        # application driving the SDK through this adapter is indistinguishable
        # from bare SDK use on every telemetry dimension — same sdk, same
        # sdk_version, same endpoint — which is the gap the registry closes.
        #
        # Here rather than at module import, and the distinction is the point:
        # importing the module says the adapter is INSTALLED, constructing one
        # says it is IN USE, and only the second is adoption signal.
        #
        # The heartbeat fires on the client's first outbound REQUEST, not at
        # client construction, so a registration made here — necessarily after
        # the client exists and before any call through it — reaches the very
        # first ping. No I/O; one insert into a set that deduplicates.
        #
        # A LITERAL, not WorkflowSource.LANGGRAPH.value even where that holds
        # the same text: WorkflowSource is a platform API value the
        # orchestrator interprets, this is a telemetry value the checkpoint
        # buckets. Deriving one from the other would let a rename in the
        # workflow API silently repoint an analytics dimension.
        register_adapter("langgraph")
        _get_callback_class()  # Fail fast if langchain-core missing
        self._graph = graph
        self._client = client
        self._workflow_name = workflow_name
        self._source = source
        self._metadata = metadata
        self._trace_id = trace_id
        self._node_config = node_config or {}
        self._exclude_nodes = exclude_nodes
        self._govern_tools = govern_tools

    def _make_adapter(self) -> AxonFlowLangGraphAdapter:
        # Always auto_block=True — the wrapper uses callbacks which cannot
        # return False to the graph, so non-blocking semantics are unsafe.
        return AxonFlowLangGraphAdapter(
            self._client,
            self._workflow_name,
            source=self._source,
            auto_block=True,
        )

    def _make_callback(self, adapter: AxonFlowLangGraphAdapter) -> Any:
        cls = _get_callback_class()
        return cls(
            adapter,
            node_config=self._node_config,
            exclude_nodes=self._exclude_nodes,
            govern_tools=self._govern_tools,
        )

    @staticmethod
    def _merge_callbacks(config: dict[str, Any] | None, callback: Any) -> dict[str, Any]:
        """Merge governance callback into the user's RunnableConfig."""
        config = dict(config or {})
        existing = config.get("callbacks") or []
        if not isinstance(existing, list):
            existing = list(existing)
        config["callbacks"] = [*existing, callback]
        return config

    async def ainvoke(
        self,
        input: Any,  # noqa: A002
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke the graph asynchronously with governance.

        Each call creates a new AxonFlow workflow, checks gates before
        every node, records step completions, and finalises the workflow.

        Args:
            input: The graph input (passed through to ``graph.ainvoke``).
            config: Optional LangChain ``RunnableConfig``.  User-provided
                callbacks are preserved alongside the governance callback.
            **kwargs: Extra keyword arguments forwarded to the underlying
                graph's ``ainvoke``.

        Returns:
            The graph output, identical to calling ``graph.ainvoke`` directly.

        Raises:
            WorkflowBlockedError: If a node is blocked by policy.
            WorkflowApprovalRequiredError: If a node requires approval.
        """
        adapter = self._make_adapter()
        await adapter.start_workflow(metadata=self._metadata, trace_id=self._trace_id)

        callback = self._make_callback(adapter)
        merged_config = self._merge_callbacks(config, callback)

        try:
            result = await self._graph.ainvoke(input, config=merged_config, **kwargs)
        except WorkflowBlockedError:
            await adapter.abort_workflow(reason="Governance block")
            raise
        except WorkflowApprovalRequiredError:
            # Do NOT abort — leave the workflow resumable so the user
            # can approve the step and call adapter.wait_for_approval()
            raise
        except Exception as exc:
            await adapter.fail_workflow(reason=f"Exception: {exc}")
            raise
        else:
            await adapter.complete_workflow()
            return result

    def invoke(
        self,
        input: Any,  # noqa: A002
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke the graph synchronously with governance.

        Creates a dedicated event loop to run the async pipeline.
        See :meth:`ainvoke` for full documentation.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.ainvoke(input, config, **kwargs))
        finally:
            loop.close()

    async def astream(
        self,
        input: Any,  # noqa: A002
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Stream the graph asynchronously with governance.

        Yields chunks from the underlying graph's ``astream`` method.
        Workflow lifecycle is managed automatically.
        """
        adapter = self._make_adapter()
        await adapter.start_workflow(metadata=self._metadata, trace_id=self._trace_id)

        callback = self._make_callback(adapter)
        merged_config = self._merge_callbacks(config, callback)

        try:
            async for chunk in self._graph.astream(input, config=merged_config, **kwargs):
                yield chunk
            await adapter.complete_workflow()
        except WorkflowBlockedError:
            await adapter.abort_workflow(reason="Governance block")
            raise
        except WorkflowApprovalRequiredError:
            # Do NOT abort — leave resumable for approval flow
            raise
        except Exception as exc:
            await adapter.fail_workflow(reason=f"Exception: {exc}")
            raise


def wrap_langgraph(
    graph: Any,
    *,
    client: AxonFlow,
    workflow_name: str,
    source: WorkflowSource = WorkflowSource.LANGGRAPH,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
    node_config: dict[str, NodeConfig] | None = None,
    exclude_nodes: set[str] | frozenset[str] | None = None,
    govern_tools: bool = True,
) -> GovernedGraph:
    """Wrap a compiled LangGraph graph with AxonFlow governance.

    Returns a :class:`GovernedGraph` whose ``ainvoke`` / ``invoke`` /
    ``astream`` methods enforce policy gates at every node transition.

    Blocked nodes raise :class:`WorkflowBlockedError`; nodes requiring
    approval raise :class:`WorkflowApprovalRequiredError` (the workflow
    is left in a resumable state so the caller can approve and retry).

    Args:
        graph: A compiled LangGraph ``StateGraph`` (from ``graph.compile()``).
        client: An :class:`~axonflow.AxonFlow` client instance.
        workflow_name: Human-readable workflow name registered with AxonFlow.
        source: Workflow source (default ``langgraph``).
        metadata: Workflow-level metadata dict.
        trace_id: External trace ID for correlation (LangSmith, OTel, etc.).
        node_config: Per-node overrides.  Keys are node names; values are
            :class:`NodeConfig` instances.
        exclude_nodes: Node names that should skip governance entirely.
            Defaults to ``{"__start__", "__end__"}``.
        govern_tools: If ``True`` (default), individual tool calls within
            nodes are gate-checked via ``check_tool_gate`` /
            ``tool_completed``.

    Returns:
        A reusable :class:`GovernedGraph` instance.

    Example::

        governed = wrap_langgraph(compiled, client=client, workflow_name="demo")
        result = await governed.ainvoke({"query": "Hello"})
    """
    resolved_excludes = frozenset(exclude_nodes) if exclude_nodes is not None else _DEFAULT_EXCLUDE

    return GovernedGraph(
        graph,
        client=client,
        workflow_name=workflow_name,
        source=source,
        metadata=metadata,
        trace_id=trace_id,
        node_config=node_config or {},
        exclude_nodes=resolved_excludes,
        govern_tools=govern_tools,
    )
