"""本地版免登录服务的访问守卫：挡 DNS 重绑定与跨站写请求，不影响本机正常使用"""
from runtime.guard import host_allowed, origin_allowed


def test_host_check_blocks_rebinding_domains():
    assert host_allowed("127.0.0.1:8000")
    assert host_allowed("localhost:8000")
    assert host_allowed("[::1]:8000")
    assert host_allowed("192.168.1.20:8000")  # 局域网按 IP 访问不可能是重绑定
    assert host_allowed("app.localhost")
    assert not host_allowed("evil.example.com:8000")
    assert not host_allowed("")


def test_origin_check_requires_same_site_for_writes():
    assert origin_allowed("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert origin_allowed("http://localhost:5173", "127.0.0.1:8000")  # 前端开发服务器在 CORS 白名单
    assert not origin_allowed("https://evil.example.com", "127.0.0.1:8000")
    assert not origin_allowed("null", "127.0.0.1:8000")


def test_guard_rejects_rebinding_and_cross_site_writes(client):
    assert client.get("/api/system/capabilities").status_code == 200

    rebinding = client.get("/api/system/credentials", headers={"Host": "evil.example.com:8000"})
    assert rebinding.status_code == 403
    assert rebinding.json()["code"] == "LOCAL_ACCESS_DENIED"

    cross_site = client.put(
        "/api/system/credentials",
        json={"provider": "deepseek", "api_key": "attacker-key"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert cross_site.status_code == 403

    fetch_metadata = client.post(
        "/api/system/credentials/test",
        json={"provider": "deepseek"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert fetch_metadata.status_code == 403

    same_origin = client.put(
        "/api/system/credentials",
        json={"provider": "deepseek", "api_key": "user-key"},
        headers={"Origin": "http://testserver"},
    )
    assert same_origin.status_code == 200


def test_oversized_request_rejected_before_body_is_read(client):
    from config import settings

    too_big = str((settings.max_upload_mb + 2) * 1024 * 1024)
    response = client.post(
        "/api/system/credentials/test",
        content=b"{}",
        headers={"Content-Length": too_big, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_changing_endpoint_host_requires_reentering_key(client, credentials):
    credentials.set_credentials("deepseek", api_key="user-key")

    moved = client.put(
        "/api/system/credentials",
        json={"provider": "deepseek", "base_url": "https://collector.example.com/v1"},
    )
    assert moved.status_code == 400
    # 设置页按 code 显示中文或英文提示
    assert moved.json()["code"] == "BASE_URL_HOST_CHANGED"
    assert credentials.base_url("deepseek") != "https://collector.example.com/v1"

    # 同主机改路径、或连同新 Key 一起改，都照常保存
    same_host = client.put(
        "/api/system/credentials",
        json={"provider": "deepseek", "base_url": "https://api.deepseek.com/v2"},
    )
    assert same_host.status_code == 200
    with_key = client.put(
        "/api/system/credentials",
        json={"provider": "deepseek", "base_url": "https://proxy.example.com/v1", "api_key": "proxy-key"},
    )
    assert with_key.status_code == 200
    assert credentials.base_url("deepseek") == "https://proxy.example.com/v1"
