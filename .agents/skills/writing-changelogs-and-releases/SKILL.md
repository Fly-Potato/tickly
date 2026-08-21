---
name: writing-changelogs-and-releases
description: Use when the user asks to write or update a changelog, release notes, version announcement, release checklist, Git tag, GHCR publication, deployment handoff, or rollback note for the Tickly repository. Trigger on Chinese requests mentioning 变更日志、更新日志、发布说明、发版、版本、tag、镜像发布 or 回滚, even when the user does not explicitly say “changelog”.
---

# Tickly 变更日志与发版

## 核心原则

以当前仓库和实际运行证据为准，先区分“代码已变更”“提交已创建”“tag 已创建”“Actions 已成功”“镜像已发布”“线上已验收”这几个独立状态。任何没有证据的状态都必须写成待确认或未完成，不能为了让发布说明完整而推测。

## 触发后先读取

从仓库根目录开始，按任务需要读取：

1. `AGENTS.md`、`README.md` 和相关 `docs/` 发布/部署文档。
2. `package.json`、`mise.toml`、`.github/workflows/`、`compose*.yaml`，确认真实命令、触发条件、镜像标签和权限。
3. `git status --short`、`git log`、目标范围的 `git diff`，确认变更边界和提交状态。
4. 只有用户要求报告实际发布结果时，才查询 Actions、GHCR 或部署主机；仓库配置不能代替远端证据。

不要把历史路线图中的计划、旧提交说明或 README 示例当作当前发布事实。发现文档与代码冲突时，以当前代码、当前 workflow 和可复现命令为准，并在结果中指出冲突。

## 证据分级

| 状态 | 最低证据 | 可以写成 |
| --- | --- | --- |
| 代码变更 | 当前 diff 和测试 | 已实现/已验证的代码行为 |
| 本地提交 | `git log -1`、干净或明确的 `git status` | 已提交，附 commit SHA |
| 版本发布 | 远端可见的 `v*` tag 和对应 commit | 已创建版本 tag |
| CI 发布 | Actions run 成功，且检查与发布 job 均成功 | workflow 已发布 |
| 镜像发布 | 三个目标镜像的 tag、digest、平台和 attestation | 三镜像已发布 |
| 线上验收 | 目标环境的 pull、migration、health/smoke 日志 | 线上版本已验收 |

本地 commit 不等于已 push；tag 不等于 workflow 成功；镜像 push 不等于 Package Public；镜像存在不等于线上部署完成。

## 变更日志格式

仓库没有既有 `CHANGELOG.md` 时，只有用户明确要求落盘才创建它。默认使用中文 Keep a Changelog 风格，版本号遵循仓库已有约定：

```markdown
# 更新日志

## [Unreleased]

### 新增
- [用户可感知功能或工程能力]（`path/to/file`）

### 变更
- [行为、接口或发布流程变化]（`path/to/file`）

### 工程
- [非用户可感知但会影响维护、性能、安全、CI、依赖、测试或部署的变化]（`path/to/file`）

### 修复
- [修复的具体问题和影响边界]（`path/to/file`）

### 修复
- [修复的具体问题和影响边界]（`path/to/file`）

### 验证
- `[command]`：通过（`N` tests passed）

## [vX.Y.Z] - YYYY-MM-DD

### 发布
- 三套 GHCR 镜像：tag、digest、平台和 attestation（仅在有远端证据时填写）
```

写条目时：

- 变更日志需要详尽覆盖本次有工程价值的变化，但用语保持简洁；不要只记录用户界面功能。
- 至少按“用户/接口与数据/工程/验证与发布”区分信息。没有某一类时省略该小节，不用空标题凑格式。
- 工程类条目包括 CI 触发与检查范围、依赖和工具链、重构、测试覆盖、性能、安全、Docker/Compose、migration 和文档契约等非用户可感知变化。
- 每条只表达一个事实，优先使用“动作 + 对象 + 结果/影响”的短句；同一变化不要在多个小节重复。
- 每条尽量关联真实路径、接口、命令或 commit SHA；多个紧密相关文件可合并为一个路径组。
- 把 breaking change、migration、配置变量、部署前置条件和回滚边界单独写清楚。
- 纯格式化、自动生成文件、无行为影响的机械重命名和重复测试输出属于噪声，可以省略；不要把有维护或发布影响的内部变化误删。
- 测试和发布证据放在“验证/发布”中，不能把测试计划写成已完成结果。

## 发版流程

用户要求发版但未明确授权远端操作时，只生成清单和命令，不创建 tag、不 push、不触发远端发布。

按以下顺序核对：

1. 读取最新正式 `v*` tag、该 tag 之后的 commit/变更日志和当前工作树；确认目标 commit、未提交改动、migration 风险和回滚版本。
2. 根据历史改动给出一个推荐版本号，并简短说明依据：向后兼容的新功能通常升 minor；修复、内部工程变化、文档或 CI 通常升 patch；不兼容 API、数据或部署契约升 major。没有现有 tag 时从 `v0.1.0` 或仓库已有版本约定开始，不要凭空假设成熟度。
3. 在创建 tag 或执行远端动作前，必须询问用户确认推荐版本号。用户确认推荐值时继续；用户直接给出其他版本号时，校验 `vX.Y.Z` 格式、是否已存在以及是否高于最近正式版本，校验通过后采用用户给出的版本。版本冲突或无法判断时先停下询问，不替用户改号。
4. 运行与改动范围匹配的检查；涉及发布时至少执行仓库当前要求的 Web/API/MCP 检查，并保留实际输出。
5. 检查 `git diff --check`、工作树状态和待提交路径；提交消息使用中文 Conventional Commit。
6. 获得版本确认和明确远端授权后，再创建 `vX.Y.Z` tag 和 push。不得覆盖已发布的正式 tag；修复发布新 patch 版本。
7. `v*` tag 触发 `.github/workflows/release-images.yml`：先通过发布前全量检查，再构建 API、MCP、Web 的 `linux/amd64` 和 `linux/arm64` 镜像。
8. 发布后逐一核对三个 GHCR 镜像的同一版本 tag、digest、source revision、平台 manifest 和 attestation。任一镜像缺失时，状态只能写成部分发布/不可部署。
9. 线上部署需要额外核对明确 `TICKLY_IMAGE_TAG`、migration、三个服务健康状态、同源路由和真实 HTTPS smoke；这些证据缺失时不要写“生产已完成”。

## 仓库检查命令

从仓库根目录优先使用 mise：

```bash
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:web
mise exec -- pnpm test:api
mise exec -- pnpm test:mcp
git diff --check
```

按影响范围选择命令，不为了填充日志运行无关检查。涉及 Compose、Dockerfile 或发布配置时，再执行仓库 `AGENTS.md` 要求的 Compose 检查；不要在输出中展开密钥或完整生产环境变量。

## 输出模板

用户只要分析：说明当前事实、证据、缺口和建议动作，不改文件、不提交、不创建 tag。

用户要写日志：说明修改的确切文件、覆盖的版本/范围、证据来源和未确认项；完成后运行 `git diff --check`。

用户要发版：先按“历史改动 → 推荐版本号与依据 → 等待确认”输出；确认后再按“版本与 commit → 检查结果 → tag/Actions → 三镜像 → 部署/smoke → 回滚点”执行，每一项标注已确认、未执行或失败原因。

## 常见误判

- 用 `git log` 推断 GitHub Actions 已成功。
- 用 workflow 文件推断 GHCR Package 已 Public。
- 只确认 Web 镜像就声称 API、MCP、Web 整组发布完成。
- 把 `latest` 当作不可变生产版本。
- 把 migration 可执行写成 migration 已在目标数据库执行。
- 把“建议创建 tag/push”写成已经创建或已经推送。
