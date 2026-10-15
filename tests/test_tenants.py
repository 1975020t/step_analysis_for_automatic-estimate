"""Several companies in one system: each sees, numbers and changes only its own data and files."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import tests.test_platform as platform_tests
from api.main import create_app
from src.db.models import Staff
from src.db.session import open_database
from src.db.seed import seed
from src.services.settings import Settings
from src.tenancy import check_tenant, current_tenant, tenant_scope

TOKENS = "alpha=token-a,beta=token-b"


@pytest.fixture
def app_clients(tmp_path, _history_copy):
    storage = tmp_path / "storage"
    settings = Settings(data_dir=platform_tests.ROOT / "data", storage_dir=storage, history_path=_history_copy,
                        quote_log_path=storage / "quote_log.csv", api_token="token-default", tenant_tokens=TOKENS)
    app = create_app(settings, reader_factory=lambda: platform_tests.RecordedReader(platform_tests.RECORDED))
    with TestClient(app) as base:
        clients = {}
        for tenant, token in (("default", "token-default"), ("alpha", "token-a"), ("beta", "token-b")):
            client = TestClient(app)
            client.headers["Authorization"] = f"Bearer {token}"
            clients[tenant] = client
        yield clients, storage, base


def test_a_company_sees_only_its_own_drawings_estimates_files_and_jobs(app_clients):
    clients, storage, _ = app_clients
    a, b = clients["alpha"], clients["beta"]
    drawing_id = platform_tests.register(a, "ALPHA-001", pdf=platform_tests.DRAWING_PDF)
    started = a.post("/api/estimates", json={"drawing_id": drawing_id, "customer": "株式会社テスト", "quantity": 10,
                                             "material": "SPCC"}).json()
    assert platform_tests.wait(a, started["job_id"])["status"] == "done"
    q = a.get(f"/api/estimates/{started['estimate_id']}").json()
    drawing = a.get(f"/api/drawings/{drawing_id}").json()
    revision = drawing["revisions"][0]
    assert revision["analysis"]["status"] != "error"  # the background job ran as alpha

    assert [d["drawing_no"] for d in a.get("/api/drawings").json()] == ["ALPHA-001"]
    assert b.get("/api/drawings").json() == [] and b.get("/api/cases").json()["cases"] == []
    assert len(a.get("/api/estimates").json()) == 1 and b.get("/api/estimates").json() == []
    for path in (f"/api/drawings/{drawing_id}", f"/api/estimates/{q['id']}", f"/api/files/{revision['pdf_file_id']}/raw",
                 f"/api/revisions/{revision['id']}/preview.png", f"/api/jobs/{started['job_id']}"):
        assert a.get(path).status_code == 200, path
        assert b.get(path).status_code == 404, path
    r = b.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"quantity": 1}})
    assert r.status_code == 404
    # alpha's files are under tenants/alpha/, nothing of alpha at the places of the default company
    assert (storage / "tenants" / "alpha" / "uploads" / revision["pdf_file_id"]).is_dir()
    assert not (storage / "uploads" / revision["pdf_file_id"]).exists()


def test_numbers_and_masters_are_per_company(app_clients):
    clients, _, _ = app_clients
    a, b = clients["alpha"], clients["beta"]
    qa = platform_tests.estimate(a, platform_tests.register(a, "N-001"), material="SPCC")
    qb = platform_tests.estimate(b, platform_tests.register(b, "N-001"), material="SPCC")  # same drawing number
    case_a = a.get("/api/cases").json()["cases"][0]["number"]
    case_b = b.get("/api/cases").json()["cases"][0]["number"]
    assert case_a == case_b and qa["number"] == qb["number"]  # each company numbers from the start

    row = next(r for r in a.get("/api/master-tables/materials").json() if r["code"] == "SPCC")
    r = a.put("/api/master-tables/materials/SPCC", json={"values": {"price_per_kg": "999"}, "version": row["version"]})
    assert r.status_code == 200, r.text
    price = {t: next(r for r in c.get("/api/master-tables/materials").json() if r["code"] == "SPCC")["price_per_kg"]
             for t, c in clients.items()}
    assert price["alpha"] == "999" and price["beta"] == price["default"] != "999"


def test_a_new_company_starts_with_the_masters_but_no_sample_data(app_clients):
    clients, _, _ = app_clients
    d, b = clients["default"], clients["beta"]
    assert d.get("/api/history").json()["total"] > 2000 and b.get("/api/history").json()["total"] == 0
    assert d.get("/api/partners").json() and b.get("/api/partners").json() == []
    assert len(b.get("/api/master-tables/materials").json()) == len(d.get("/api/master-tables/materials").json())
    assert [s["name"] for s in b.get("/api/statuses").json()] == [s["name"] for s in d.get("/api/statuses").json()]
    company = {r["code"]: r["value"] for r in b.get("/api/master-tables/company").json()}
    assert company and not any(company.values())  # its own details, to be filled in


def test_a_token_decides_the_company_and_others_are_refused(app_clients):
    clients, _, base = app_clients
    assert base.get("/api/drawings").status_code == 401
    wrong = base.get("/api/drawings", headers={"Authorization": "Bearer token-x"})
    assert wrong.status_code == 401
    assert clients["default"].get("/api/drawings").status_code == 200
    assert base.get("/api/health").status_code == 200


def test_tokens_and_company_ids_are_checked():
    s = Settings(api_token="t0", tenant_tokens="alpha=t1, beta = t2")
    assert s.tokens() == {"t0": "default", "t1": "alpha", "t2": "beta"}
    for bad in ("alpha", "alpha=", "Alpha=t1", "../x=t1"):
        with pytest.raises(ValueError):
            Settings(tenant_tokens=bad).tokens()
    for bad in ("", "A", "a/b", "a" * 41, "-a"):
        with pytest.raises(ValueError):
            check_tenant(bad)


def test_the_session_reads_and_changes_only_the_current_company(tmp_path):
    sessions = open_database(f"sqlite:///{tmp_path / 'app.db'}")
    for tenant in ("alpha", "beta"):
        with tenant_scope(tenant), sessions() as s:
            seed(s, platform_tests.ROOT / "data", sample=False)
            s.add(Staff(name="山田"))  # the same name in two companies
            s.commit()
    with tenant_scope("alpha"), sessions() as s:
        names = {(st.tenant_id, st.name) for st in s.scalars(select(Staff))}
        assert names == {("alpha", "担当者"), ("alpha", "山田")}
        beta_id = s.execute(select(Staff.id).execution_options(all_tenants=True)
                            .where(Staff.tenant_id == "beta", Staff.name == "山田")).scalar_one()
        assert s.get(Staff, beta_id) is None
        s.execute(update(Staff).values(active=False))
        s.commit()
    with tenant_scope("beta"), sessions() as s:
        assert all(st.active for st in s.scalars(select(Staff)))
    assert current_tenant() == "default"
