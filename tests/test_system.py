def test_health(test_client):
    r = test_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data

def test_version(test_client):
    r = test_client.get("/version")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data and "version" in data and "env" in data

def test_root_redirects_to_docs(test_client):
    r = test_client.get("/", allow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "/docs" in r.headers.get("location", "")
