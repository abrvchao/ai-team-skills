# WorkBuddy 可用国产模型能力矩阵（Model Matrix）

> 数据来源：`codebuddy --help` 的 `--model` 选项实测输出（CLI v2.156.0，2026-10-11）
> ⚠️ **重要免责说明**：本文严格区分「已实测」与「未实测」。
> 由于 CLI 独立认证尚未打通（见 `WORKBUDDY_HANDOFF_RESULT.md`），**所有模型均未能完成一次真实调用**，
> 因此能力列（上下文长度 / 工具调用 / 计费）**不做推测性填写**，一律标注为「待实测」。
> 本矩阵中唯一确凿的事实是：**这些模型 ID 被当前 CLI 版本声明为可选**。

---

## 一、实测确认的模型清单

以下 21 个模型 ID 来自 `codebuddy --help` 的原样输出（已逐字核对）：

```
fast-model, balanced-model, deep-model,
hy4-preview, hy3, hy3-x, space-bunny,
deepseek-v4.1-flash, deepseek-v4-pro,
glm-5.3, glm-5.3-flash, glm-5.3-flashx, glm-5.2, glm-5.1, glm-5v-turbo,
minimax-m3-pay,
kimi-k3-2, kimi-k2.8-preview, kimi-k2.7, kimi-k2.6,
step-5-preview
```

---

## 二、能力矩阵

图例：
- **可用性** = `CLI 声明`（实测，来自 help 输出）/ `账号可用`（待实测，需认证打通后逐个验证）
- **提供商** 一列标注为「推断」的，是由模型 ID 命名惯例推断，**未经官方文档确认**
- **计费** 一列：`-pay` 后缀为官方命名语义（`minimax-m3-pay`）；其余一律「未知」

| 模型 ID | 提供商 | 可用性 | 适合编程 | 长上下文 | 工具调用 | 计费 | 可直接自动运行 |
|---|---|---|---|---|---|---|---|
| `fast-model` | WorkBuddy 内部档位（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 默认放行 |
| `balanced-model` | WorkBuddy 内部档位（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 默认放行 |
| `deep-model` | WorkBuddy 内部档位（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 默认放行 |
| `deepseek-v4.1-flash` | DeepSeek（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 默认放行（**默认模型**） |
| `deepseek-v4-pro` | DeepSeek（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `glm-5.3` | 智谱 GLM（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `glm-5.3-flash` | 智谱 GLM（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `glm-5.3-flashx` | 智谱 GLM（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `glm-5.2` | 智谱 GLM（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `glm-5.1` | 智谱 GLM（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `glm-5v-turbo` | 智谱 GLM 视觉档（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `kimi-k3-2` | 月之暗面 Kimi（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `kimi-k2.8-preview` | 月之暗面 Kimi（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许（preview） |
| `kimi-k2.7` | 月之暗面 Kimi（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `kimi-k2.6` | 月之暗面 Kimi（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `minimax-m3-pay` | MiniMax（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | **付费**（命名语义） | **默认拦截** |
| `step-5-preview` | 阶跃星辰 Step（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许（preview） |
| `hy4-preview` | 腾讯混元（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许（preview） |
| `hy3` | 腾讯混元（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `hy3-x` | 腾讯混元（推断） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |
| `space-bunny` | 未识别（代号） | CLI 声明 | 待实测 | 待实测 | 待实测 | 未知 | 允许 |

---

## 三、任务书点名模型的对齐情况

任务书要求调查：DeepSeek / 通义千问 Qwen / 小米 MiMo / 智谱 GLM / Kimi / 豆包 Doubao。

| 任务书点名 | 在 WorkBuddy CLI 里 | 说明 |
|---|---|---|
| DeepSeek | ✅ `deepseek-v4.1-flash`、`deepseek-v4-pro` | 已声明 |
| 智谱 GLM | ✅ `glm-5.3` 系列共 6 个 | 已声明 |
| Kimi | ✅ `kimi-k3-2`、`kimi-k2.6/2.7/2.8-preview` | 已声明 |
| 腾讯混元 | ✅ `hy3`、`hy3-x`、`hy4-preview`（推断为混元） | 已声明 |
| **小米 MiMo** | ❌ **未出现在 WorkBuddy CLI 模型清单** | MiMo 是 AI Team 侧已注册的独立 Worker（自有凭据），不经 WorkBuddy |
| **通义千问 Qwen** | ❌ **未出现在 WorkBuddy CLI 模型清单** | 同上，Qwen 是 AI Team 侧独立 Worker（`qwen-node-worker`，当前 available=true） |
| 豆包 Doubao | ❌ 未出现在 WorkBuddy CLI 清单 | AI Team 侧已有 `doubao-node-worker` |

**结论**：WorkBuddy 这条通道提供的是 **DeepSeek / GLM / Kimi / MiniMax / Step / 混元**；
而 **Qwen / MiMo / Doubao 已经在 AI Team 里以独立 Worker 形式存在**，不需要（也无法）经由 WorkBuddy 调用。

---

## 四、Worker 侧的模型调度策略

代码位置：`workbuddy-node-worker/src/index.js`

```js
SUPPORTED_MODELS   // 上表全部 21 个 ID；不在清单内的模型直接拒绝（model_not_allowed）
PAID_MODELS        // ["minimax-m3-pay"]，默认拦截（paid_model_blocked）
DEFAULT_SAFE_MODELS// fast-model / balanced-model / deep-model / deepseek-v4.1-flash
DEFAULT_MODEL      // deepseek-v4.1-flash
```

选择优先级：`task.metadata.model` → `WORKBUDDY_MODEL` → 默认值。

这样 Supervisor 就能**按任务能力与成本挑模型**，而不是被写死在一个模型上：

```json
{ "metadata": { "model": "glm-5.3" } }
```

---

## 五、待补全（认证打通后）

1. 逐个模型跑一次最小调用，记录真实响应。
2. 实测并回填：上下文窗口、工具调用支持、典型延迟。
3. 向用户确认账号的实际计费/额度规则，回填「计费」列。
4. 把 `space-bunny` 的真实身份问清楚（当前无法识别）。
