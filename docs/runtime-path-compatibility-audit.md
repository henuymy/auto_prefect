# 运行路径兼容层审计（已下线）

旧 `runtime/...` 解析函数已删除。运行目录参数只接受相对于 `runtime.root` 的
白名单路径；`runtime/...`、绝对路径和包含 `..` 的路径会被拒绝，不会回退到
项目工作区。

| 原边界 | 状态 | 现行契约 |
| --- | --- | --- |
| `services/runtime_paths.py` 旧解析 API | 已下线 | 使用 `resolve_runtime_relative_path()` |
| Dashboard V2 导入脚本 `--bundle` | 已迁移 | `modules/<module>/output/...` |
| Dashboard V2 切换审计 `--bundle`、`--approvals` | 已迁移 | `modules/<module>/output/...` |

复核命令：

```powershell
rg -n 'resolve_runtime_path\(|validate_runtime_path\(' services scripts tests
python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_dashboard_v2_indicator_config_import.py tests/test_dashboard_v2_cutover_audit.py -q
```
