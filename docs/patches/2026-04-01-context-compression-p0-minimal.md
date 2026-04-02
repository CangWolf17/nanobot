# Context Compression P0 Minimal Patch

## Goal

从 `src` 基线提炼出一份只修上下文压缩主因的最小 patch，保留：

- MiniMax memory consolidation 专用 provider/model 路由
- pre-reply / background consolidation timeout
- fail-open
- over-budget 时 recent-history fallback

不纳入本 patch：

- `workspace_bridge` / `builtin` / `workspace_agent_cmd` 命令注入链
- `loop.py` 中与 slash-command postprocess 相关的逻辑
- `max_chunk_messages`
- 非 MiniMax 的 archive provider 泛化

## Baseline

- Source baseline: `/home/admin/.nanobot/workspace/tmp/src_snapshot`
- Target tree: `/home/admin/nanobot-main-live`

## Included Files

- `nanobot/config/schema.py`
- `nanobot/cli/commands.py`
- `nanobot/agent/loop.py`
- `nanobot/agent/memory.py`
- `tests/cli/test_commands.py`
- `tests/agent/test_loop_consolidation_tokens.py`
- `tests/agent/test_memory_consolidation_types.py`

## Patch Digest

1. `schema.py`
   - 新增 `MemoryConsolidationConfig`
   - 将 `memory` 挂到根配置 `Config`

2. `commands.py`
   - 新增 MiniMax Anthropic-compatible archive provider 构造
   - `gateway()` / `agent()` 显式传入 `memory_config / archive_provider / archive_model`

3. `loop.py`
   - `AgentLoop` 接入 `memory_config / archive_provider / archive_model`
   - 在主回复前先跑 best-effort pre-reply consolidation
   - timeout / exception 时 fail-open
   - preflight 失败且仍 over-budget 时，仅带最近窗口历史继续回复
   - 回复后异步跑 background consolidation

4. `memory.py`
   - consolidation 改走 archive provider/model
   - 为 outer timeout 增加 failure accounting / raw archive degrade
   - 提供 `prompt_budget()` / `target_prompt_tokens()` / `is_over_budget()`
   - `maybe_consolidate_by_tokens()` 返回 `bool`，供 loop 做 fail-open 分支判断

## Selected Diff

### `nanobot/config/schema.py`

```diff
@@
 class MCPServerConfig(Base):
     tool_timeout: int = 30
     enabled_tools: list[str] = Field(default_factory=lambda: ["*"])

+class MemoryConsolidationConfig(Base):
+    """Memory consolidation runtime controls."""
+
+    enabled: bool = True
+    pre_reply_timeout_seconds: float = 8.0
+    background_timeout_seconds: float = 20.0
+    recent_history_fallback_messages: int = 80
+    model: str = ""
@@
 class Config(BaseSettings):
     gateway: GatewayConfig = Field(default_factory=GatewayConfig)
     tools: ToolsConfig = Field(default_factory=ToolsConfig)
+    memory: MemoryConsolidationConfig = Field(default_factory=MemoryConsolidationConfig)
```

### `nanobot/cli/commands.py`

```diff
@@
-def _make_provider(config: Config):
+def _infer_minimax_anthropic_base(api_base: str | None) -> str:
+    ...
+
+class MiniMaxAnthropicCompatProvider:
+    ...
+
+def _make_memory_archive_provider(config: Config):
+    archive_model = config.memory.model.strip() if config.memory.model else ""
+    if not archive_model or not archive_model.lower().startswith("minimax/"):
+        return None
+    ...
+
+def _make_provider(config: Config, model_override: str | None = None):
@@
     bus = MessageBus()
     provider = _make_provider(config)
+    archive_model = config.memory.model.strip() if config.memory.model else ""
+    archive_provider = _make_memory_archive_provider(config) or provider
@@
         channels_config=config.channels,
         timezone=config.agents.defaults.timezone,
+        memory_config=config.memory,
+        archive_provider=archive_provider,
+        archive_model=archive_model or config.agents.defaults.model,
@@
     bus = MessageBus()
     provider = _make_provider(config)
+    archive_model = config.memory.model.strip() if config.memory.model else ""
+    archive_provider = _make_memory_archive_provider(config) or provider
@@
         channels_config=config.channels,
         timezone=config.agents.defaults.timezone,
+        memory_config=config.memory,
+        archive_provider=archive_provider,
+        archive_model=archive_model or config.agents.defaults.model,
```

### `nanobot/agent/loop.py`

说明：这里只导出 compression 相关 hunk；`workspace_agent_cmd` / postprocess 逻辑不属于本 patch。

```diff
@@
 if TYPE_CHECKING:
-    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, WebSearchConfig
+    from nanobot.config.schema import (
+        ChannelsConfig,
+        ExecToolConfig,
+        MemoryConsolidationConfig,
+        WebSearchConfig,
+    )
@@
     def __init__(
         ...
         channels_config: ChannelsConfig | None = None,
         timezone: str | None = None,
+        memory_config: MemoryConsolidationConfig | None = None,
+        archive_provider: LLMProvider | None = None,
+        archive_model: str | None = None,
     ):
-        from nanobot.config.schema import ExecToolConfig, WebSearchConfig
+        from nanobot.config.schema import ExecToolConfig, MemoryConsolidationConfig, WebSearchConfig
@@
         self.exec_config = exec_config or ExecToolConfig()
         self.cron_service = cron_service
         self.restrict_to_workspace = restrict_to_workspace
+        self.memory_config = memory_config or MemoryConsolidationConfig()
+        self.archive_provider = archive_provider or provider
+        self.archive_model = archive_model or self.memory_config.model or (model or provider.get_default_model())
@@
         self.memory_consolidator = MemoryConsolidator(
             ...
+            archive_provider=self.archive_provider,
+            archive_model=self.archive_model,
         )
@@
+    async def _run_pre_reply_consolidation(self, session: Session) -> bool:
+        ...
+
+    async def _run_background_consolidation(self, session: Session) -> None:
+        ...
+
+    def _select_history_for_reply(self, session: Session, *, preflight_ok: bool) -> list[dict[str, Any]]:
+        ...
@@
-            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
+            await self._run_pre_reply_consolidation(session)
@@
-            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
+            self._schedule_background(self._run_background_consolidation(session))
@@
-        await self.memory_consolidator.maybe_consolidate_by_tokens(session)
+        preflight_ok = await self._run_pre_reply_consolidation(session)
@@
-        history = session.get_history(max_messages=0)
+        history = self._select_history_for_reply(session, preflight_ok=preflight_ok)
@@
-        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
+        self._schedule_background(self._run_background_consolidation(session))
```

### `nanobot/agent/memory.py`

```diff
@@
     def __init__(
         ...
         max_completion_tokens: int = 4096,
+        archive_provider: LLMProvider | None = None,
+        archive_model: str | None = None,
     ):
         self.store = MemoryStore(workspace)
         self.provider = provider
         self.model = model
+        self.archive_provider = archive_provider or provider
+        self.archive_model = archive_model or model
@@
     async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
-        return await self.store.consolidate(messages, self.provider, self.model)
+        return await self.store.consolidate(messages, self.archive_provider, self.archive_model)
+
+    async def handle_timeout(self, session: Session, *, phase: str) -> None:
+        ...
@@
-    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
+    def estimate_session_prompt_tokens(
+        self,
+        session: Session,
+        *,
+        max_history_messages: int = 0,
+    ) -> tuple[int, str]:
         ...
+
+    def prompt_budget(self) -> int:
+        ...
+
+    def target_prompt_tokens(self) -> int:
+        ...
+
+    def is_over_budget(self, session: Session, *, max_history_messages: int = 0) -> tuple[bool, int, str]:
+        ...
@@
-    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
+    async def maybe_consolidate_by_tokens(self, session: Session) -> bool:
         ...
+        try:
+            ...
+        except asyncio.CancelledError:
+            ...
+            return False
```

## Test Coverage Kept In This Patch

### `tests/cli/test_commands.py`

- `test_agent_passes_memory_config_and_archive_provider`
- `test_make_memory_archive_provider_prefers_minimax_anthropic`
- `test_make_memory_archive_provider_returns_none_for_non_minimax`
- `test_gateway_passes_memory_config_and_archive_provider`

### `tests/agent/test_loop_consolidation_tokens.py`

- `test_preflight_consolidation_before_llm_call`
- `test_pre_reply_consolidation_timeout_fail_open_still_replies`
- `test_pre_reply_consolidation_timeout_records_failure_count`
- `test_repeated_timeout_cancellation_degrades_to_raw_archive_and_advances_offset`
- `test_failed_preflight_over_budget_uses_recent_history_fallback`

### `tests/agent/test_memory_consolidation_types.py`

- `test_cancelled_error_propagates_from_store_consolidate`

## Validation

执行命令：

```bash
uv run --extra dev python -m pytest tests/cli/test_commands.py tests/agent/test_loop_consolidation_tokens.py tests/agent/test_memory_consolidation_types.py
```

结果：

- `74 passed`

## Notes

- 当前 `~/.nanobot/config.json` 里仍有历史键 `memory.maxChunkMessages`。
- 该键不属于本 repo patch，也没有在本次最小 patch 中继续支持或清理。
- 如果后续要做配置面清理，应单独处理，不和本 patch 混在一起。
