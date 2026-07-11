# 开发环境与依赖审计设计

## 目标

将项目开发环境明确为 Conda `base`（Python 3.13）与 NVM 管理的 Node.js 20 LTS；保留所有当前业务代码、脚本或配置正在使用的直接依赖，并把依赖清单、锁文件和安装方式的职责写入文档。

## 审计结论

Python 的直接运行依赖均有明确用途：

- `prefect`：通报与驾驶舱 Flow、Worker、部署管理。
- `requests`、`selenium`：业务接口请求与浏览器登录会话。
- `asyncpg`：Prefect PostgreSQL 元数据库连接和连接检查。
- `SQLAlchemy`、`PyMySQL`、`alembic`：驾驶舱 MySQL 访问、模型和迁移。
- `fastapi`、`python-multipart`、`uvicorn`：配置中心 API、文件上传和 ASGI 服务。
- `openpyxl`、`PyMuPDF`、`pywin32`：Excel 报表处理、PDF 渲染、Windows Excel COM 自动化。

前端生产与构建依赖也均在 React 组件、Tailwind/PostCSS、Vite 或 TypeScript 配置中使用。本次不删除仍被引用的直接依赖，也不把通报、截图或浏览器登录改为可选能力。

## 环境约定

- Python：使用 Conda `base` 的 Python 3.13。项目通过 `python -m <module>` 调用工具，不依赖用户级 Scripts 目录在 `PATH` 中。
- Node：使用 NVM 管理的 Node.js 20 LTS；在 `frontend/` 执行 `npm install`。
- 完整本地开发：需要 Node.js、Prefect、MySQL、Microsoft Edge；涉及真实通报时还需要 Microsoft Excel。
- 单元测试：使用项目可写目录作为 pytest 临时目录，避免受保护的用户临时目录导致测试失败。

## 文件职责

- `pyproject.toml`：唯一人工维护的 Python 直接依赖和开发工具声明。
- `requirements.lock`：从 `pyproject.toml` 解析出的完整运行时精确依赖树。
- `requirements-dev.lock`：运行时依赖加 `dev` extra 的完整精确依赖树。
- `requirements.txt`：兼容 `pip install -r requirements.txt` 的运行时入口，仅转引运行锁文件。
- `frontend/package.json`：前端直接生产与开发依赖声明。
- `frontend/package-lock.json`：npm 解析出的精确前端依赖树。

## 实施范围

1. 修正启动和初始化脚本，移除对 `auto-notify` 环境的优先/硬编码，改为明确使用已激活的 Conda `base` 或当前 `python`。
2. 增加开发环境契约测试，检查脚本不再设置与 Conda `base` 用户级安装冲突的 `PYTHONNOUSERSITE=1`，并检查 README 解释所有依赖清单。
3. 更新 README 的 Python、Node、安装、验证和依赖文件说明；明确完整开发功能的系统前置条件。
4. 保持 `pyproject.toml` 的直接依赖集合不变；仅在确认生成过程产生差异时更新锁文件。

## 验证

- 运行环境契约测试与完整 Python 测试套件，pytest 临时目录位于项目 `runtime/test-tmp`。
- 在 Node 20 环境中执行 `npm install`、`npm run typecheck` 和 `npm run build`。
- 使用 `python -m pip check` 记录 base 环境中与项目无关的既存包冲突；该检查不作为项目依赖锁一致性的失败条件。
