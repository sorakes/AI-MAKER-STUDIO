"""Jobs: o banco é a fonte da verdade (sobrevive a reinício); cache em memória só para os jobs ativos."""
import asyncio
import json
import time

import pytest

from app import db, jobs


def _insert(job: dict):
    db.run("INSERT INTO jobs(id, user_id, created, status, data) VALUES(?,?,?,?,?) "
           "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
           (job["id"], job["user_id"], job["created"], job["status"], json.dumps(job)))


def _job(jid, uid, status="queued", steps=None, **extra):
    return {"id": jid, "user_id": uid, "created": time.time(), "status": status, "progress": 0, "detail": None,
            "error": None, "spec": {"prompt": "p", "node_id": "", "name": "n"}, "steps": steps or [],
            "est_cost": 0, "output": None, "media": None, "keyframe": None, **extra}


@pytest.fixture
def owner(make_user):
    return make_user("jobs-db@test.local")


@pytest.fixture(autouse=True)
def _no_leftover_live_jobs():
    """resume_all varre a tabela inteira: jobs de testes não podem ficar 'queued/running' para o próximo."""
    yield
    db.run("UPDATE jobs SET status = 'done' WHERE id LIKE 'job-%' AND status IN ('queued', 'running')")
    jobs.JOBS.clear()


def test_pending_is_counted_from_database_not_memory(owner):
    step = {"kind": "video", "status": "pending", "est": 1.5}
    jid = f"job-pend-{time.time_ns()}"
    _insert(_job(jid, owner["id"], "queued", [step]))
    jobs.JOBS.pop(jid, None)  # sem cache
    out = jobs._pending(owner["id"])
    assert out["videos"] >= 1 and out["spend"] >= 1.5


def test_get_after_restart_uses_database(owner):
    jid = f"job-rest-{time.time_ns()}"
    _insert(_job(jid, owner["id"], "done"))
    jobs.JOBS.clear()  # simula reinício do processo
    user = {"id": owner["id"], "role": "user"}
    assert jobs.get(jid, user)["status"] == "done"


def test_resume_marks_unrecoverable_jobs_interrupted(owner):
    jid = f"job-int-{time.time_ns()}"
    _insert(_job(jid, owner["id"], "running", [{"kind": "video", "status": "running", "handle": None}]))
    asyncio.run(_resume_inside_loop())
    row = db.one("SELECT status, data FROM jobs WHERE id = ?", (jid,))
    assert row["status"] == "interrupted"
    assert "reiniciou" in json.loads(row["data"])["error"]


async def _resume_inside_loop():
    await jobs.resume_all()


def test_resume_restarts_live_job_with_handle(owner, monkeypatch):
    jid = f"job-live-{time.time_ns()}"
    _insert(_job(jid, owner["id"], "running", [{"kind": "video", "status": "running", "handle": {"id": "x"}}]))
    started = []

    async def fake_run(job, user, resume=False):
        started.append((job["id"], resume))

    monkeypatch.setattr(jobs, "_run", fake_run)

    async def go():
        await jobs.resume_all()
        await asyncio.sleep(0)  # deixa as tarefas criadas começarem
    asyncio.run(go())
    assert (jid, True) in started


def test_start_from_worker_thread_schedules_on_server_loop(owner, monkeypatch):
    """render_pose e assets.generate rodam em threads e chamam jobs.submit: o render tem de cair no loop."""
    import threading

    started = []

    async def fake_run(job, user, resume=False):
        started.append((job["id"], threading.current_thread() is threading.main_thread()))

    monkeypatch.setattr(jobs, "_run", fake_run)

    async def main():
        jobs.bind_loop(asyncio.get_running_loop())
        job = {"id": "job-thread01", "user_id": owner["id"], "created": time.time(), "status": "queued",
               "steps": [], "spec": {}}
        t = threading.Thread(target=jobs.start, args=(job, {"id": owner["id"]}))
        t.start()
        t.join()
        await asyncio.sleep(0.05)  # deixa o loop executar o create_task agendado

    asyncio.run(main())
    assert started == [("job-thread01", True)]  # a tarefa roda no loop (thread principal), não na worker
    jobs._LOOP = None
