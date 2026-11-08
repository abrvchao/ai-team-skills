# M2 China Social Controlled Experiment（M2 中国社媒受控实验）

Date（日期）: 2026-10-03  
Backlog（待办）: `M2-CHINA-001`  
Mode（模式）: **read-only / anonymous（只读 / 匿名）**

## Contract（实验合同）

本实验只验证真实访问边界，不构建 production scraper（生产爬虫）：

- 不登录；
- 不注入 Cookie；
- 不复用个人浏览器 profile（个人浏览器配置）；
- 不点击、发布、点赞、关注或提交表单；
- 所有结果统一标记 `terms_class=experimental_personal_research（实验性个人研究）`；
- 失败和登录墙也是有效 evidence（证据），不能为了“成功”绕平台风控。

机器可读记录：`m2-controlled-observations.json`。

## Real Browser Worker run（真实浏览器执行器运行）

三条任务通过 Bridge（桥接服务）真实排队、租赁和执行，并临时使用三个 Browser Worker（浏览器执行器）并行加速：

| Platform（平台） | Task（任务） | Browser result（浏览器结果） |
| --- | --- | --- |
| Weibo（微博） | `task_8cd14be0ed134dd69c04` | failed（失败）；0 DOM / 0 screenshot（无页面结构/截图） |
| Douyin（抖音） | `task_5332fa03d28e02d2ba98` | failed（失败）；0 DOM / 0 screenshot（无页面结构/截图） |
| Xiaohongshu（小红书） | `task_3c29986a96a8ebac0979` | failed（失败）；0 DOM / 0 screenshot（无页面结构/截图） |

这些失败没有被伪装成成功；Browser Report（浏览器报告）和 worker logs（执行器日志）均保留为 artifact（成果）。

## Anonymous HTTP control（匿名 HTTP 对照）

为区分 platform access boundary（平台访问边界）和 headless runtime compatibility（无界面浏览器兼容性），同机追加无 Cookie 的只读 HTTP 对照：

- **Weibo（微博）**：`302 → 200`，最终进入 `passport.weibo.com` 的 `Sina Visitor System（新浪访客系统）`，页面出现“登录”。结论：anonymous public-web（匿名公开网页）存在明确登录边界。
- **Douyin（抖音）**：原 URL 匿名返回 HTTP `200`、约 `72,914 bytes` HTML shell（页面壳），无重定向；但当前 Chrome headless（Chrome 无界面模式）未产出 DOM/截图。结论：当前证据支持“HTTP 页面壳可达”，**不支持**“可稳定获取结构化数据”。
- **Xiaohongshu（小红书）**：`302 → 200`，最终进入 `/login`，页面出现“登录”和 `Forbidden（禁止访问）` 标记。结论：anonymous public-web 存在明确登录/访问边界。

## Product decision（产品决策）

M2-CHINA-001 的目标是验证受控真实世界边界，**不是要求三站必须抓到业务数据**。本次实验因此完成，但不会把任何路径提升成 production provider（生产数据提供器）。

生产策略保持：

- Weibo（微博）：只有商业数据 API / 明确 licensed（授权）合同路径才考虑生产；
- Douyin（抖音）：继续单独验证 official API（官方接口）权限，不把 200 HTML 页面壳当数据合同；
- Xiaohongshu（小红书）：在存在官方/授权/合同路径前保持 deferred（延期）；
- Browser/cookie automation（浏览器/Cookie 自动化）不成为 Radar 核心 SaaS 依赖。
