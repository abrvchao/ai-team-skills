# AI Team Local Install（AI Team 本地安装）

## Install（安装）

```bash
cd ai-team-orchestrator
./scripts/install-local.sh
```

先演练、不修改环境：

```bash
./scripts/install-local.sh --dry-run
```

## Verify（验证）

```bash
./scripts/verify-install.sh
```

验证会检查 Python、Node、npm、MCP SDK（MCP 开发包）、核心控制面文件、每个 Node Worker 的 `package-lock.json（依赖锁文件）` 和 ADB（安卓调试桥）。

Python MCP（模型上下文协议）运行时使用 `requirements-mcp.lock.txt（完整依赖锁文件）` 固定当前已验证的直接与传递依赖；`requirements-mcp.txt` 只保留顶层依赖意图。

Node Worker 安装统一使用 `npm ci（严格锁定依赖安装）`，而不是会重新解析依赖版本的 `npm install`。通用 `node-worker` 与所有专用 Worker 都必须有 `package-lock.json（依赖锁文件）`。没有连接 Android device（安卓设备）会显示 `BLOCKED（阻塞）`，但不会把基础安装判成失败。

## Uninstall（卸载）

```bash
./scripts/uninstall-local.sh
```

卸载只删除 `.venv` 和 Worker 的 `node_modules`，**不会删除项目代码、`.ai-team-runs` 成果、账号配置或密钥文件**。

## Credential boundary（凭据边界）

安装脚本不会读取、复制或生成任何 API Key（API 密钥）。Grok 等 Worker 的凭据继续使用本机 Secret File（密钥文件）或各自受控环境变量。


## Release Bundle（发布包）

构建确定性发布包：

```bash
python build_release_bundle.py --output /tmp/ai-team-v1.zip
python build_release_bundle.py --output /tmp/ai-team-v1.zip --verify
```

发布包使用显式白名单，只包含控制面源码、Worker 源码、依赖锁文件、脚本与 V1 文档；不会打包密钥、`.venv`、`node_modules`、运行状态或任务成果。包内带 `RELEASE_MANIFEST.json（发布清单）` 与 `SHA256SUMS.txt（文件完整性校验）`。
