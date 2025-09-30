def test_qcm_flow_with_fallback_corpus(test_client):
    # Start (fileId peut ne pas exister : engine utilisera le fallback)
    body = {"fileId": "does_not_exist", "difficulty": "medium", "total": 3}
    r = test_client.post("/v1/qcm/start", json=body)
    assert r.status_code == 200, r.text
    start = r.json()
    assert "sessionId" in start and "question" in start
    sess = start["sessionId"]
    q = start["question"]
    assert "id" in q and "choices" in q

    # Answer 1
    ans_body = {"questionId": q["id"], "choiceIndex": 0}
    r = test_client.post(f"/v1/qcm/{sess}/answer", json=ans_body)
    assert r.status_code == 200, r.text
    ans = r.json()
    assert "isCorrect" in ans and "nextIndex" in ans

    # Boucler jusqu'au résultat (2 autres réponses arbitraires)
    if ans["nextIndex"] < 3 and ans.get("nextQuestion"):
        q2 = ans["nextQuestion"]
        r = test_client.post(f"/v1/qcm/{sess}/answer", json={"questionId": q2["id"], "choiceIndex": 1})
        assert r.status_code == 200
        ans2 = r.json()
        if ans2["nextIndex"] < 3 and ans2.get("nextQuestion"):
            q3 = ans2["nextQuestion"]
            r = test_client.post(f"/v1/qcm/{sess}/answer", json={"questionId": q3["id"], "choiceIndex": 2})
            assert r.status_code == 200

    # Result
    r = test_client.get(f"/v1/qcm/{sess}/result")
    assert r.status_code == 200
    res = r.json()
    assert res["total"] == 3
    assert "score" in res and "details" in res
