import json
import os
import time
import urllib.error
import urllib.request

base = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8001")


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def main() -> None:
    print("health", req("GET", "/api/health"))
    code, cfg = req("GET", "/api/site/config")
    print("config", code, cfg.get("app_name"), cfg.get("yandex_oauth"))
    email = "qa@example.com"
    password = "password123"
    code, reg = req(
        "POST",
        "/api/web/register",
        {"email": email, "password": password, "name": "QA"},
    )
    print("register", code, reg.get("user", {}).get("email"), reg.get("detail"))
    token = reg.get("token")
    if not token and code == 409:
        code, login = req("POST", "/api/web/login", {"email": email, "password": password})
        print("login_existing", code)
        token = login.get("token")
        reg = login
    assert token, reg
    code, me = req("GET", "/api/web/me", token=token)
    print("me", code, me.get("user", {}).get("email"))
    print("campaigns", req("GET", "/api/web/direct/campaigns", token=token))
    code, rule = req(
        "POST",
        "/api/web/rules",
        {
            "name": "CPA",
            "rule_type": "cpa_above",
            "threshold_value": 500,
            "dry_run": True,
        },
        token=token,
    )
    print("rule", code, rule.get("id"), rule.get("detail"))
    assert rule.get("id"), rule
    print("run", req("POST", f"/api/web/rules/{rule['id']}/run?dry_run=true", token=token))
    print("dup", req("POST", "/api/web/register", {"email": "qa@example.com", "password": "password123"}))
    print("login", req("POST", "/api/web/login", {"email": "qa@example.com", "password": "password123"})[0])
    # Аналитика живёт в рабочих пространствах (одно на счётчик Метрики); у нового пользователя их нет.
    code, workspaces = req("GET", "/api/web/workspaces", token=token)
    print("workspaces", code, len(workspaces.get("items") or []))
    assert code == 200 and workspaces.get("items") == [], workspaces
    code, overview = req("GET", "/api/web/overview", token=token)
    print("overview", code, len(overview.get("workspaces") or []), len(overview.get("problems") or []))
    assert code == 200 and "totals" in overview and "problems" in overview, overview
    code, client = req(
        "POST",
        "/api/web/clients",
        {"name": "Клиент QA", "kpi_type": "cpa", "kpi_target": 900, "monthly_budget_rub": 50000},
        token=token,
    )
    print("client", code, client.get("id"), client.get("detail"))
    assert code == 200 and client.get("id"), client
    code, patched = req("PATCH", f"/api/web/clients/{client['id']}", {"archived": True}, token=token)
    assert code == 200 and patched.get("archived") is True, patched
    code, clients = req("GET", "/api/web/clients", token=token)
    assert code == 200 and any(c["id"] == client["id"] for c in clients.get("items", [])), clients
    code, _ = req("DELETE", f"/api/web/clients/{client['id']}", token=token)
    assert code == 200
    code, sync = req("POST", "/api/web/analytics/sync", {"days": 1}, token=token)
    print("analytics_sync", code, sync.get("job", {}).get("status"), sync.get("detail"))
    assert code == 200 and sync.get("job", {}).get("id"), sync
    job = sync["job"]
    # Задачу выполняет сервис worker; ждём завершения.
    for _ in range(60):
        if job["status"] in ("done", "error"):
            break
        time.sleep(1)
        code, job = req("GET", f"/api/web/jobs/{job['id']}", token=token)
        assert code == 200, job
    print("analytics_job", job["status"], job.get("error_code"), job.get("result"))
    assert job["status"] == "done", job
    # oauth exchange: неверный code
    print("oauth_bad", req("POST", "/api/web/oauth/exchange", {"code": "nope"}))
    print("SMOKE_OK")


if __name__ == "__main__":
    main()
