from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from infrastructure import dashboard_mysql


def valid_env() -> dict[str, str]:
    return {
        "DASHBOARD_MYSQL_HOST": "db.internal",
        "DASHBOARD_MYSQL_PORT": "3307",
        "DASHBOARD_MYSQL_DATABASE": "dashboard",
        "DASHBOARD_MYSQL_USER": "dashboard_app",
        "DASHBOARD_MYSQL_PASSWORD": "p@ss:/word",
        "DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS": "8",
        "DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS": "45",
    }


def test_settings_require_independent_dashboard_variables():
    with pytest.raises(dashboard_mysql.DashboardMySQLConfigError) as exc_info:
        dashboard_mysql.DashboardMySQLSettings.from_env({})

    assert "DASHBOARD_MYSQL_HOST" in str(exc_info.value)
    assert "DASHBOARD_MYSQL_PASSWORD" in str(exc_info.value)


def test_settings_build_encoded_sqlalchemy_url_without_losing_password():
    settings = dashboard_mysql.DashboardMySQLSettings.from_env(valid_env())

    parsed = make_url(settings.sqlalchemy_url().render_as_string(hide_password=False))

    assert parsed.drivername == "mysql+pymysql"
    assert parsed.host == "db.internal"
    assert parsed.port == 3307
    assert parsed.database == "dashboard"
    assert parsed.password == "p@ss:/word"
    assert parsed.query["charset"] == "utf8mb4"
    assert settings.connect_timeout_seconds == 8
    assert settings.io_timeout_seconds == 45


def test_public_summary_never_contains_password():
    settings = dashboard_mysql.DashboardMySQLSettings.from_env(valid_env())

    summary = settings.public_summary()

    assert "password" not in summary
    assert summary["database"] == "dashboard"


def test_check_reports_unconfigured_without_connecting():
    result = dashboard_mysql.check_dashboard_mysql({})

    assert result["ok"] is False
    assert result["configured"] is False


def test_check_connection_returns_server_details(monkeypatch):
    class FakeResult:
        def mappings(self):
            return self

        def one(self):
            return {
                "connection_ok": 1,
                "database_name": "dashboard",
                "server_version": "8.0.test",
            }

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement):
            return FakeResult()

    class FakeEngine:
        disposed = False

        def connect(self):
            return FakeConnection()

        def dispose(self):
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(
        dashboard_mysql,
        "create_dashboard_engine",
        lambda settings: engine,
    )

    result = dashboard_mysql.check_dashboard_mysql(valid_env())

    assert result["ok"] is True
    assert result["configured"] is True
    assert result["database_name"] == "dashboard"
    assert engine.disposed is True


def test_shared_dashboard_engine_is_created_once(monkeypatch):
    created = []

    class FakeEngine:
        disposed = False

        def dispose(self):
            self.disposed = True

    def fake_create():
        engine = FakeEngine()
        created.append(engine)
        return engine

    dashboard_mysql.dispose_dashboard_engine()
    monkeypatch.setattr(
        dashboard_mysql,
        "create_dashboard_engine",
        fake_create,
    )

    first = dashboard_mysql.get_dashboard_engine()
    second = dashboard_mysql.get_dashboard_engine()

    assert first is second
    assert len(created) == 1

    dashboard_mysql.dispose_dashboard_engine()
    assert first.disposed is True


def test_dashboard_mysql_lock_acquires_and_releases_named_lock():
    calls = []

    class FakeConnection:
        results = iter([1, 1])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalar(self, statement, params):
            calls.append((str(statement), params))
            return next(self.results)

    class FakeEngine:
        dialect = type("Dialect", (), {"name": "mysql"})()

        def connect(self):
            return FakeConnection()

    with dashboard_mysql.dashboard_mysql_lock(
        FakeEngine(), lock_name="dashboard-test", wait_seconds=3
    ) as lock:
        assert lock == {"name": "dashboard-test", "backend": "mysql"}

    assert "GET_LOCK" in calls[0][0]
    assert calls[0][1]["wait_seconds"] == 3
    assert "RELEASE_LOCK" in calls[1][0]


def test_dashboard_mysql_lock_times_out_without_entering_body():
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalar(self, _statement, _params):
            return 0

    class FakeEngine:
        dialect = type("Dialect", (), {"name": "mysql"})()

        def connect(self):
            return FakeConnection()

    with pytest.raises(TimeoutError, match="MySQL 驾驶舱采集锁"):
        with dashboard_mysql.dashboard_mysql_lock(FakeEngine()):
            raise AssertionError("lock body must not run")
