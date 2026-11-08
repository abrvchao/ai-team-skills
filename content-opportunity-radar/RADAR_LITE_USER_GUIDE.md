# Radar Lite User Guide（Radar 精简版使用指南）

Radar Lite 的默认产品路径只有四步：

> 启动 → Scan（扫描）→ 查看机会与证据 → Handoff（交给 AI Team）

## 1. 启动

```bash
cd content-opportunity-radar
python3 radar_product.py
```

默认地址：

```text
http://127.0.0.1:8787
```

默认只绑定本机。Radar Lite 的写动作也只接受 localhost（本机）请求。

## 2. Scan（扫描）

在页面顶部填写 Radar scope（雷达范围），例如 `AI Agents`，然后选择 Language（语言）。

当前 Dashboard 一等支持：

- 中文 — `zh / CN`
- English — `en / US`
- 日本語 — `ja / JP`
- 한국어 — `ko / KR`
- Français — `fr / FR`
- Deutsch — `de / DE`
- Español — `es / ES`
- Português — `pt / BR`

语言切换会同时影响 Lite Scan（精简扫描）的 `language + country` 参数；中文 / 英文 / 日文 / 韩文还会同步切换 Dashboard 的核心操作文案。

选好后点击 **Run Lite Scan**。

Lite 模式固定使用当前 native（原生）发现链，不会自动打开 GSC、AIHOT、China-social、Google Ads 或新的 Provider（数据源）。

扫描结束后页面自动刷新 Top Opportunities（优先机会）。

## 3. 查看机会与证据

点击任意 Opportunity（机会）可以查看机会分、排名分、置信度、Why now（为什么现在）、Evidence（证据）、来源确认、历史变化和 Research Pack（研究包，如存在）。

## 4. Handoff（交给 AI Team）

打开一个机会后点击 **Generate HANDOFF.json**。

Radar 会使用现有 deterministic handoff builder（确定性交接生成器），把 `HANDOFF.json` 与 `HANDOFF.md` 写入固定的 `.radar/lite/handoffs/` 目录。

Radar 本身不会自动启动 AI Team Worker（执行器）。后续由 AI Team 的 Handoff Intake / Goal Runner（交接接入 / 目标执行入口）决定是否创建 Product Contract（产品合同）并启动交付。

## Product boundary（产品边界）

当前 Lite 版本刻意不做 GSC、AIHOT、China-social、Google Ads、新 Provider 或新 scoring（评分）维度。

这些能力保留为 Deferred / Restartable（延后 / 可重启），只有真实使用证明需要时再开启。
