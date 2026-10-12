"""The file storage: a local folder or S3 (moto stands in for S3; no AWS call)."""
from __future__ import annotations

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

import tests.test_api as api_tests
import tests.test_platform as platform_tests
from api.main import create_app
from src.services.settings import Settings
from src.services.storage import FileStore, LocalBlobStore, S3BlobStore, blob_store, check_key

BUCKET = "estimate-test"


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})
        yield client


def stores(tmp_path, s3):
    return [LocalBlobStore(tmp_path / "blobs"), S3BlobStore(BUCKET, "tenant-a", client=s3)]


def test_both_stores_keep_bytes_and_hand_out_a_local_file(tmp_path, s3):
    for blobs in stores(tmp_path, s3):
        assert not blobs.exists("documents/Q-1/quote.pdf")
        with pytest.raises(KeyError):
            blobs.get("documents/Q-1/quote.pdf")
        blobs.put("documents/Q-1/quote.pdf", b"%PDF-1")
        assert blobs.exists("documents/Q-1/quote.pdf") and blobs.get("documents/Q-1/quote.pdf") == b"%PDF-1"
        with blobs.local_file("documents/Q-1/quote.pdf") as path:
            assert path.name == "quote.pdf" and path.read_bytes() == b"%PDF-1"
        with pytest.raises(KeyError):
            with blobs.local_file("documents/Q-2/quote.pdf"):
                pass


def test_the_temporary_copy_of_an_s3_object_is_removed(s3):
    blobs = S3BlobStore(BUCKET, "", client=s3)
    blobs.put("a/b.step", b"ISO-10303-21;")
    with blobs.local_file("a/b.step") as path:
        assert path.exists()
    assert not path.exists()


def test_s3_keys_are_under_the_prefix(s3):
    S3BlobStore(BUCKET, "/tenant-a/", client=s3).put("uploads/x.pdf", b"1")
    assert [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]] == ["tenant-a/uploads/x.pdf"]


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../x", "a/../../x", "a//b", "a/./b", "a/b c"])
def test_keys_cannot_leave_the_store(key):
    with pytest.raises(KeyError):
        check_key(key)


def test_file_store_on_s3(s3):
    files = FileStore(S3BlobStore(BUCKET, "p", client=s3))
    meta = files.save(b"ISO-10303-21;", "../部品 A.step")
    assert meta["filename"] == "部品_A.step" and meta["kind"] == "step" and meta["size"] == 13
    assert files.meta(meta["file_id"]) == meta
    assert files.get(meta["file_id"]) == (b"ISO-10303-21;", meta)
    with files.local_path(meta["file_id"]) as path:
        assert path.name == "部品_A.step"
    for bad in ("f_0000000000000000", "../f", "x"):
        with pytest.raises(KeyError):
            files.meta(bad)


def test_the_storage_url_chooses_the_store(tmp_path, s3):
    assert isinstance(blob_store("", tmp_path), LocalBlobStore)
    store = blob_store(f"s3://{BUCKET}/tenants/a", tmp_path)
    assert isinstance(store, S3BlobStore) and store.bucket == BUCKET and store.prefix == "tenants/a"
    for bad in ("s3://", "gs://bucket", "/srv/files"):
        with pytest.raises(ValueError):
            blob_store(bad, tmp_path)


# ---------------------------------------------------------------- the API with S3: the same flows as the folder
@pytest.fixture
def s3_settings(tmp_path, s3):
    return api_tests.settings_for(tmp_path, storage_url=f"s3://{BUCKET}/estimate")


def nothing_in_the_folder(settings) -> bool:
    return not any(settings.storage_dir.joinpath(d).exists() for d in ("uploads", "documents", "viewer"))


def test_api_flows_with_s3(s3_settings, s3):
    with TestClient(create_app(s3_settings, reader_factory=api_tests.FakeReader)) as client:
        api_tests.test_step_analysis_quote_document_and_similar_quotes(client)
        api_tests.test_drawing_reading_with_a_fake_reader_feeds_the_quote_and_the_document(client)
        api_tests.test_step_is_returned_as_glb(client)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert any(k.startswith("estimate/uploads/") for k in keys)
    assert any(k.startswith("estimate/documents/") for k in keys)
    assert any(k.startswith("estimate/viewer/") for k in keys)
    assert nothing_in_the_folder(s3_settings)


def test_screen_flows_with_s3(tmp_path, _history_copy, s3):
    storage = tmp_path / "storage"
    settings = Settings(data_dir=platform_tests.ROOT / "data", storage_dir=storage, history_path=_history_copy,
                        quote_log_path=storage / "quote_log.csv", storage_url=f"s3://{BUCKET}/screens")
    app = create_app(settings, reader_factory=lambda: platform_tests.RecordedReader(platform_tests.RECORDED))
    with TestClient(app) as client:
        platform_tests.test_amounts_are_those_of_the_existing_calculation_and_the_same_on_screen_api_and_documents(client)
        platform_tests.test_a_delivery_note_follows_the_status_of_the_case(client)
        drawing_id = platform_tests.register(client, "S3-PREVIEW", step=platform_tests.DRAWING_STEP,
                                             pdf=platform_tests.DRAWING_PDF)
        revision_id = client.get(f"/api/drawings/{drawing_id}").json()["revisions"][0]["id"]
        first = client.get(f"/api/revisions/{revision_id}/preview.png?width=300")
        assert first.status_code == 200 and first.content.startswith(b"\x89PNG")
        assert client.get(f"/api/revisions/{revision_id}/preview.png?width=300").content == first.content
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert any(k.startswith("screens/uploads/") for k in keys)
    assert any(k.startswith("screens/viewer/") and k.endswith("_300.png") for k in keys)
    assert nothing_in_the_folder(settings)
