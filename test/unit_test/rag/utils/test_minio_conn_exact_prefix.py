from importlib import import_module
from inspect import getclosurevars
from unittest.mock import Mock

from minio.error import S3Error


def _storage(*, bucket="musemind-ragflow", prefix_path="provider"):
    import_module("common.settings")
    from rag.utils import minio_conn

    storage_class = getclosurevars(minio_conn.RAGFlowMinio).nonlocals["cls"]
    storage = storage_class.__new__(storage_class)
    storage.bucket = bucket
    storage.prefix_path = prefix_path
    storage.conn = Mock()
    return storage


def _missing_key():
    return S3Error(
        "NoSuchKey",
        "missing",
        "resource",
        "request-id",
        "host-id",
        None,
    )


def test_single_bucket_health_uses_prefix_scoped_list_without_head_bucket():
    storage = _storage()
    storage.conn.list_objects.return_value = iter(())

    assert storage.health() is True

    storage.conn.list_objects.assert_called_once_with("musemind-ragflow", prefix="provider/", recursive=False)
    storage.conn.bucket_exists.assert_not_called()


def test_single_bucket_health_fails_closed_when_prefix_probe_is_denied():
    storage = _storage()

    def denied_objects():
        raise RuntimeError("denied")
        yield

    storage.conn.list_objects.return_value = denied_objects()

    assert storage.health() is False
    storage.conn.bucket_exists.assert_not_called()


def test_single_bucket_logical_bucket_probe_stays_inside_exact_prefix():
    storage = _storage()
    storage.conn.list_objects.return_value = iter(())

    assert storage.bucket_exists("dataset-id") is True

    storage.conn.list_objects.assert_called_once_with("musemind-ragflow", prefix="provider/dataset-id/", recursive=False)
    storage.conn.bucket_exists.assert_not_called()


def test_object_existence_uses_exact_stat_without_head_bucket():
    storage = _storage()
    storage.conn.stat_object.return_value = Mock()

    assert storage.obj_exist("dataset-id", "document.bin") is True

    storage.conn.stat_object.assert_called_once_with("musemind-ragflow", "provider/dataset-id/document.bin")
    storage.conn.bucket_exists.assert_not_called()


def test_missing_object_fails_closed_without_head_bucket():
    storage = _storage()
    storage.conn.stat_object.side_effect = _missing_key()

    assert storage.obj_exist("dataset-id", "missing.bin") is False
    storage.conn.bucket_exists.assert_not_called()


def test_single_bucket_copy_does_not_probe_or_create_physical_bucket():
    storage = _storage()
    storage.conn.stat_object.return_value = Mock()

    assert storage.copy("source", "a.bin", "destination", "b.bin") is True

    storage.conn.bucket_exists.assert_not_called()
    storage.conn.make_bucket.assert_not_called()
    storage.conn.stat_object.assert_called_once_with("musemind-ragflow", "provider/source/a.bin")
    args = storage.conn.copy_object.call_args.args
    assert args[:2] == ("musemind-ragflow", "provider/destination/b.bin")
    assert args[2].bucket_name == "musemind-ragflow"
    assert args[2].object_name == "provider/source/a.bin"


def test_multi_bucket_copy_preserves_bucket_creation_behavior():
    storage = _storage(bucket=None, prefix_path=None)
    storage.conn.bucket_exists.return_value = False
    storage.conn.stat_object.return_value = Mock()

    assert storage.copy("source", "a.bin", "destination", "b.bin") is True

    storage.conn.bucket_exists.assert_called_once_with("destination")
    storage.conn.make_bucket.assert_called_once_with("destination")
