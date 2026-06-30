# Dashboard MySQL 集成测试

`tests/test_dashboard_mysql_integration.py` 在真实 MySQL 8 上验证：

- 从空库升级全部 Alembic 迁移；
- `metric_snapshot` 日分区和分区维护；
- MySQL 命名锁跨连接互斥；
- InnoDB 锁等待超时错误 `1205`；
- InnoDB 死锁错误 `1213`。

测试会删除目标库内的全部表，因此只接受名称以 `test` 开头，或以 `_test` / `_ci` 结尾的数据库。严禁指向生产库。

本地运行：

```powershell
$env:DASHBOARD_TEST_MYSQL_URL = "mysql+pymysql://root:password@127.0.0.1:3306/auto_notify_test"
$env:PYTHONPATH = "."
pytest -q -m mysql_integration
```

未配置 `DASHBOARD_TEST_MYSQL_URL` 时测试会明确跳过。`.github/workflows/ci.yml` 使用 MySQL 8.4 服务自动执行完整测试。

依赖锁更新：

```powershell
C:\Users\yuyu\.conda\envs\auto-notify\python.exe -m piptools compile pyproject.toml --output-file requirements.lock --strip-extras
C:\Users\yuyu\.conda\envs\auto-notify\python.exe -m piptools compile pyproject.toml --extra dev --output-file requirements-dev.lock --strip-extras
```
