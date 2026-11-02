"""Rotas migradas para o caminho async (aiosqlite): canvas com limite de abas e jobs lidos do banco."""
import json
import time

from fastapi.testclient import TestClient

from app import db
from app.main import app


def _client(login, make_user, email):
    make_user(email)
    return login(email, c=TestClient(app))


def test_canvas_tab_limit_enforced_in_transaction(login, make_user):
    c = _client(login, make_user, "abas@test.local")
    for i in range(30):
        assert c.post(f"/api/canvas?ws=video~tab{i:04d}", json={"nodes": [i]}).status_code == 200
    r = c.post("/api/canvas?ws=video~extra", json={"nodes": []})
    assert r.status_code == 400 and "30 abas" in r.json()["detail"]
    # regravar uma aba existente não conta como nova
    assert c.post("/api/canvas?ws=video~tab0000", json={"nodes": [99]}).status_code == 200
    assert c.get("/api/canvas?ws=video~tab0000").json() == {"nodes": [99]}


def test_job_is_read_from_database_when_not_in_cache(login, make_user):
    from app import jobs

    c = _client(login, make_user, "jobs@test.local")
    uid = c.get("/api/me").json()["id"]
    job = {"id": "job-testdb01", "user_id": uid, "created": time.time(), "status": "done", "progress": 100,
           "detail": None, "error": None, "spec": {"prompt": "x"}, "steps": [], "est_cost": 0,
           "output": None, "media": None, "keyframe": None}
    db.run("INSERT INTO jobs(id, user_id, created, status, data) VALUES(?,?,?,?,?)",
           (job["id"], uid, job["created"], job["status"], json.dumps(job)))
    jobs.JOBS.pop(job["id"], None)
    assert c.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
    assert any(j["id"] == job["id"] for j in c.get("/api/jobs").json())


def test_other_users_job_is_hidden(login, make_user):
    c = _client(login, make_user, "dono-job@test.local")
    outro = _client(login, make_user, "outro-job@test.local")
    uid = c.get("/api/me").json()["id"]
    job = {"id": "job-privado1", "user_id": uid, "created": time.time(), "status": "done", "steps": [], "spec": {}}
    db.run("INSERT INTO jobs(id, user_id, created, status, data) VALUES(?,?,?,?,?)",
           (job["id"], uid, job["created"], "done", json.dumps(job)))
    assert outro.get("/api/jobs/job-privado1").status_code == 404
