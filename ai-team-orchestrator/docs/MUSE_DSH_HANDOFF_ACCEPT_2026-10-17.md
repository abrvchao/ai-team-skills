# Muse 接手 DSH 死锁修复｜2026-10-17

## 接手确认

- 接手 `DSH_RECOVERY_HANDOFF_2026-10-16.md` 定义的工作：修复 DSH 启动—目录刷新死锁
- 工作分支：`muse/dsh-deadlock-fix`（不碰 `agent-handoff-20261015` 主分支）
- 协作：ChatGPT (DSH Agent) 继续其手头工作；本分支只改 DSH 相关文件

## 现状核实

- 仓库 `ai-team-orchestrator/dsh-node-worker/src/` 只有 `index.js` (331行) + `readiness.js`
- 交接文档描述的 `international-free-guard.js` 和 `index.js:374` 的 `assertInternationalFreeEligible()` **只存在于用户 Mac**，不在仓库中
- 因此修复以独立模块形式交付，Mac 端按本文 §4 步骤集成

## 修复设计

### 核心思路

把"获取最新目录和价格"从 DSH 插件运行时中解耦，做成**独立可执行的前置步骤**：

```
[步骤0] catalog-refresh.js (独立进程, 只需 https + ai-team-intl 凭据)
    ↓ 成功 → 写 ~/.dsh/.workbuddy-ai-catalog.json (fresh timestamp)
    ↓ 失败 → 标记 blocked, 不启动 worker
[步骤1] 现有 assertInternationalFreeEligible() (读本地缓存, 24h 检查)
    ↓ 通过 → 正常启动
    ↓ 拒绝 → 不启动 (保持现状)
```

关键点：
- refresh **不依赖** DSH 插件运行时，只用 Node 原生 `https` + 隔离凭据
- refresh **不发送 prompt**，只读 `/v3/config` 目录和价格
- refresh **不回退**国内/SG/其他模型；失败即停，不自动拉起 worker
- 不绕过免费门禁，不手动改缓存时间戳

### 交付文件

1. `ai-team-orchestrator/dsh-node-worker/src/catalog-refresh.js` — 独立刷新模块
2. `ai-team-orchestrator/dsh-node-worker/test/catalog-refresh.test.js` — 离线测试（零网络、零模型调用）
3. 本文件 — 集成步骤

## 修复范围外

- `freeWatch` 定时器 bug（交接文档 §3）：已记录，Mac 端修 `index.js:566-599` 时顺手改，不在本模块内
- 目录刷新本身的**用户授权**：按交接文档要求，真实 API 调用需用户明确批准；本模块只提供能力，不自动执行

## 验收标准（对应交接文档 §6）

- [ ] 离线测试全过：stale 触发刷新、刷新成功写缓存、价格非免费时拒绝、网络失败标记 blocked
- [ ] Mac 端集成后：`checkInternationalCatalog(snapshot)` 不再报 `international_catalog_stale`
- [ ] Bridge 显示真实 worker 心跳（非仅配置）
- [ ] 未授权时不自动刷新/推理
