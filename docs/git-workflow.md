# Git 分支、测试与发布流程

本文定义自动通报项目的日常开发、测试、发布和紧急修复流程。

## 目标

- `main` 始终对应已验证、可部署生产的代码。
- `develop` 是日常功能集成与测试的分支。
- 每项改动通过短生命周期分支和 Pull Request（PR）进入受保护分支。
- 每次生产发布都可以通过 Git tag 精确回溯。

## 分支职责

| 分支 | 用途 | 生命周期 |
| --- | --- | --- |
| `main` | 生产环境基线和正式发布版本 | 长期保留 |
| `develop` | 日常集成、测试和下一次待发布版本 | 长期保留 |
| `feature/<issue-id>-<short-description>` | 新功能或需求开发 | 合并后删除 |
| `bugfix/<issue-id>-<short-description>` | 测试阶段发现的缺陷修复 | 合并后删除 |
| `release/v<version>` | 发版验收期间仍需并行开发下一版时的版本冻结分支 | 发布后删除 |
| `hotfix/<incident-id>-<short-description>` | 生产环境紧急故障修复 | 合并后删除 |

## 日常开发

所有日常开发从最新的 `develop` 创建功能分支：

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c feature/PROJ-123-notification-retry
```

完成开发后，在功能分支执行测试、提交并推送：

```powershell
git add <files>
git commit -m "feat(notify): add retry handling"
git push -u origin feature/PROJ-123-notification-retry
```

在 GitHub 创建 PR：

```text
feature/PROJ-123-notification-retry -> develop
```

功能分支不得直接合并或推送到 `main`。

## 测试与常规发布

当前项目优先使用简化流程。测试开始后，暂时不要将新的功能需求合入 `develop`，以保证测试对象稳定。

```text
feature/* -> develop -> 测试/预发布环境 -> main -> 生产环境
```

1. 功能通过 PR 合入 `develop`。
2. 选择待发布的 `develop` 提交，部署到测试或预发布环境。
3. 执行自动化测试、接口和集成测试、人工回归测试及业务验收。
4. 若发现问题，从 `develop` 创建 `bugfix/<issue-id>-<short-description>`，修复后通过 PR 合回 `develop`，重新部署测试环境并回归。
5. 验收通过后，创建 PR：`develop -> main`。
6. `main` 合并后打版本标签，例如 `v1.3.0`，创建 GitHub Release，并部署生产环境。

测试环境必须运行确定的提交或构建产物；不能让环境在验收期间自动跟随不断变化的 `develop`。

## 使用发布分支的条件和流程

当测试验收需要数天、但团队还需要同时开发下一版本功能时，创建发布分支：

```text
develop -> release/v1.3.0 -> 测试/预发布环境 -> main
```

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c release/v1.3.0
git push -u origin release/v1.3.0
```

- `release/v1.3.0` 只允许修复验收缺陷、调整版本号、发布说明和部署配置；不加入新功能。
- 测试缺陷从 `release/v1.3.0` 拉出 `bugfix/<issue-id>-<short-description>`，并通过 PR 合回该 release 分支。
- 验收通过后创建 PR：`release/v1.3.0 -> main`。
- 生产发布完成后创建 PR：`release/v1.3.0 -> develop`，带回发版期间的修复。

若测试期间不需要继续开发下一版，不必创建 `release/*`，直接冻结并测试 `develop` 即可。

## 紧急生产修复

线上故障从 `main` 创建热修复分支：

```text
main -> hotfix/INC-123-authentication-timeout -> main
                                                -> develop
```

热修复只处理当前生产故障，修复后：

1. 创建 PR：`hotfix/<incident-id>-<short-description> -> main`，验证后紧急发布。
2. 创建 PR：`hotfix/<incident-id>-<short-description> -> develop`，避免后续版本丢失该修复。

## PR 对照表

| 场景 | PR 来源 | PR 目标 |
| --- | --- | --- |
| 新功能开发 | `feature/<issue-id>-<short-description>` | `develop` |
| 常规测试缺陷修复 | `bugfix/<issue-id>-<short-description>` | `develop` |
| 常规发布 | `develop` | `main` |
| 发布分支的验收缺陷修复 | `bugfix/<issue-id>-<short-description>` | `release/v<version>` |
| 使用发布分支时正式上线 | `release/v<version>` | `main` |
| 带回发布期间的修复 | `release/v<version>` | `develop` |
| 线上紧急修复 | `hotfix/<incident-id>-<short-description>` | `main` |
| 带回线上紧急修复 | `hotfix/<incident-id>-<short-description>` | `develop` |

`git pull` 只是更新当前本地分支，不是创建 PR，也不替代代码审查。

## 分支保护与合并要求

为 `main` 和 `develop` 配置 GitHub Branch Protection：

- 禁止直接推送和强制推送。
- 必须通过 PR 合并。
- 必须通过 CI，例如单元测试、构建、代码检查。
- 至少一名审阅者批准；单人维护时至少保留 CI 门禁。
- 要求分支在合并前与目标分支保持最新。

建议使用 Conventional Commits 风格的提交信息：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`。

## GitHub Release 与 release 分支的区别

- `release/v1.3.0`：为测试和发版准备而创建的临时 Git 分支。
- `v1.3.0`：指向发布代码的永久 Git tag。
- GitHub Release：基于 `v1.3.0` tag 的发布记录，可包含更新说明、安装包和校验信息。

## 当前仓库迁移建议

1. 先确认哪个分支是生产稳定基线，再将其规范为 `main`。
2. 从当前集成基线创建并推送 `develop`，将 GitHub 默认分支切换为 `develop`。
3. 为 `main` 和 `develop` 启用分支保护和 CI 门禁。
4. 现有集成分支不直接改名为 `develop`；整理、测试后创建 PR：`<legacy-integration-branch> -> develop`。
5. 后续所有新改动从 `develop` 创建 `feature/<issue-id>-<short-description>` 或 `bugfix/<issue-id>-<short-description>` 分支。
