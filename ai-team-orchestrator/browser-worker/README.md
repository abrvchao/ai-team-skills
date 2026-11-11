# Browser Worker（浏览器执行器）

M2 V0.1 使用本机 Google Chrome 的 `headless（无界面）` 模式作为 borrowed runtime（复用运行时），AI Team 只拥有薄适配层。

当前能力：

- `browser_navigation（浏览器导航）`
- `dom_capture（DOM 捕获）`
- `screenshot（截图）`
- `read_only_web_evidence（只读网页证据）`

安全边界：V0.1 不复用个人 Chrome profile（个人浏览器配置），不读取登录态，不做表单提交、发布、购买或其他副作用动作。
