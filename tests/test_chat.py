def test_chat_minimal(test_client):
    body = {
        "messages": [{"role": "user", "text": "Explique la force obligatoire du contrat"}],
        "fileContext": None
    }
    r = test_client.post("/v1/chat", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "reply" in data and isinstance(data["reply"], str) and len(data["reply"]) > 0
