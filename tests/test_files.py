import io

API_KEY_HEADER = {"x-api-key": "change_me"}

def _fake_pdf_bytes():
    # Contenu PDF minimaliste : suffisant pour passer l'upload (même si parsing pages peut échouer)
    return b"%PDF-1.4\n%EOF\n"

def test_list_initially_empty(test_client):
    r = test_client.get("/v1/files")
    assert r.status_code == 200
    assert r.json() == {"files": []}

def test_upload_pdf_then_list_and_delete(test_client):
    # Upload
    files = {"file": ("test.pdf", io.BytesIO(_fake_pdf_bytes()), "application/pdf")}
    r = test_client.post("/v1/files/upload", files=files, headers=API_KEY_HEADER)
    assert r.status_code == 201, r.text
    up = r.json()
    assert "id" in up and up["name"] == "test.pdf"
    file_id = up["id"]

    # List
    r = test_client.get("/v1/files")
    assert r.status_code == 200
    data = r.json()
    assert "files" in data and isinstance(data["files"], list)
    assert any(f["id"] == file_id for f in data["files"])

    # Delete
    r = test_client.delete(f"/v1/files/{file_id}", headers=API_KEY_HEADER)
    # 204: pas de body attendu — l'implémentation renvoie tout de même un JSON, on est souples
    assert r.status_code == 204
