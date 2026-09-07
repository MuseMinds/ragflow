"""Real service/worker method bodies and SQL; no provider startup or network."""

import ast
import contextlib
import copy
from datetime import datetime
from enum import StrEnum
import logging
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import peewee as pw
import pytest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

ROOT = Path(__file__).resolve().parents[4]


class TaskStatus(StrEnum):
    RUNNING = "1"
    CANCEL = "2"
    DONE = "3"
    FAIL = "4"


class TaskCanceledException(Exception):
    pass


@contextlib.contextmanager
def connection_context():
    # Keep the in-memory SQLite connection open across independent service calls.
    yield


def source_function(path, name, owner=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    body = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner).body if owner else tree.body
    return copy.deepcopy(next(node for node in body if isinstance(node, ast.FunctionDef) and node.name == name))


def compile_nodes(nodes, scope):
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "<actual-provider-methods>", "exec"), scope)


@pytest.fixture
def services(monkeypatch):
    database = pw.SqliteDatabase(":memory:")

    class Document(pw.Model):
        id = pw.CharField(primary_key=True)
        run = pw.CharField(null=True)
        progress = pw.FloatField(default=0)
        chunk_num = pw.IntegerField(default=0)
        progress_msg = pw.TextField(default="")
        update_time = pw.IntegerField(default=111)
        update_date = pw.DateTimeField(default=datetime(2026, 1, 1))
        process_begin_at = pw.DateTimeField(null=True)
        process_duration = pw.FloatField(default=0)

        class Meta:
            database = None

    class Task(pw.Model):
        id = pw.CharField(primary_key=True)
        doc_id = pw.CharField()
        progress = pw.FloatField(default=0)
        progress_msg = pw.TextField(default="")
        begin_at = pw.DateTimeField(null=True)
        create_time = pw.IntegerField(default=0)
        process_duration = pw.FloatField(default=0)
        task_type = pw.CharField(default="")
        priority = pw.IntegerField(default=0)

        class Meta:
            database = None

    database.bind([Document, Task])
    database.create_tables([Document, Task])
    scope = {
        "DB": SimpleNamespace(connection_context=connection_context, lock=lambda *_: contextlib.nullcontext()),
        "os": os,
        "logging": logging,
        "datetime": datetime,
        "TaskStatus": TaskStatus,
        "trim_header_by_lines": lambda value, _: value,
        "TASK_MAX_LOG_LENGTH": 3000,
        "current_timestamp": lambda: 222,
        "get_format_time": lambda: datetime(2026, 9, 7),
        "PIPELINE_SPECIAL_PROGRESS_FREEZE_TASK_TYPES": set(),
        "get_queue_length": lambda _: 0,
        "Task": Task,
        "has_canceled": lambda _: True,
        "close_connection": lambda: None,
        "TaskCanceledException": TaskCanceledException,
        "DoesNotExist": pw.DoesNotExist,
        "retry": retry,
        "stop_after_attempt": stop_after_attempt,
        "wait_exponential": wait_exponential,
        "retry_if_exception_type": retry_if_exception_type,
        "InterfaceError": pw.InterfaceError,
        "OperationalError": pw.OperationalError,
    }
    compile_nodes([source_function("api/db/services/common_service.py", "retry_db_operation")], scope)
    for owner, filename, names in (
        ("TaskService", "task_service.py", ["update_progress"]),
        ("DocumentService", "document_service.py", ["fail_if_not_cancelled", "_sync_progress"]),
    ):
        methods = [source_function("api/db/services/" + filename, name, owner) for name in names]
        # Preserve all method decorators, including the real bounded retry policy.
        compile_nodes([ast.ClassDef(name=owner, bases=[], keywords=[], body=methods, decorator_list=[])], scope)
    task_service, doc_service = scope["TaskService"], scope["DocumentService"]
    task_service.model = Task
    task_service.query = staticmethod(lambda doc_id, order_by=None: Task.select().where(Task.doc_id == doc_id))
    doc_service.model = Document
    doc_service.get_by_id = staticmethod(lambda doc_id: (True, Document.get_by_id(doc_id)))
    doc_service.update_by_id = staticmethod(lambda doc_id, info: Document.update(info).where(Document.id == doc_id).execute())
    # Only replace retry sleeping; execute the production retry predicate/count.
    doc_service.fail_if_not_cancelled.__func__.__wrapped__.retry.sleep = lambda _: None
    injected = ModuleType("api.db.services.task_service")
    injected.TaskService = task_service
    monkeypatch.setitem(sys.modules, injected.__name__, injected)
    compile_nodes([source_function("rag/svr/task_executor.py", "set_progress")], scope)
    Document.create(id="doc", run=TaskStatus.CANCEL, progress_msg="synthetic cancellation marker")
    Task.create(id="task-0", doc_id="doc", progress=0.025)
    try:
        yield SimpleNamespace(database=database, Document=Document, Task=Task, documents=doc_service, tasks=task_service, worker=scope["set_progress"], scope=scope)
    finally:
        database.close()


def test_cancelled_callback_then_125_task_sync_preserves_terminal_state(services):
    for index in range(1, 125):
        services.Task.create(id=f"task-{index}", doc_id="doc", progress=0.025)
    before = services.Document.get_by_id("doc").__data__.copy()
    with pytest.raises(TaskCanceledException):
        services.worker("task-0", prog=0.5, msg="synthetic callback")
    assert services.Task.get_by_id("task-0").progress == -1
    assert services.Document.get_by_id("doc").__data__ == before
    services.documents._sync_progress([{"id": "doc", "process_begin_at": datetime.now()}])
    assert services.Document.get_by_id("doc").__data__ == before
    assert services.Task.select().where(services.Task.progress == -1).count() == 1


@pytest.mark.parametrize("initial", [None, TaskStatus.RUNNING, TaskStatus.FAIL, TaskStatus.DONE])
def test_non_cancelled_failure_still_updates_exact_document_and_timestamps(services, initial):
    services.Document.update(run=initial).where(services.Document.id == "doc").execute()
    services.Document.create(id="other", run=TaskStatus.RUNNING)
    services.tasks.update_progress("task-0", {"progress": -1, "progress_msg": "synthetic failure"})
    doc = services.Document.get_by_id("doc")
    assert (doc.run, doc.progress, doc.chunk_num) == (TaskStatus.FAIL, -1, 0)
    assert doc.update_time == 222 and doc.update_date == datetime(2026, 9, 7)
    assert doc.progress_msg.count("synthetic failure") == 1
    assert services.Document.get_by_id("other").run == TaskStatus.RUNNING


def test_cancellation_after_task_read_wins_atomic_document_update(services, monkeypatch):
    services.Document.update(run=TaskStatus.RUNNING).execute()
    original = services.database.execute_sql
    cancellation = []

    def execute(sql, params=None, *args, **kwargs):
        if sql.startswith('UPDATE "document"') and not cancellation:
            # Task lookup and log update have already completed; cancellation is
            # committed just before the failure SQL is evaluated by the database.
            assert services.Task.get_by_id("task-0").progress == -1
            cancellation.append(True)
            original('UPDATE "document" SET "run" = ?, "progress_msg" = ? WHERE "id" = ?', [TaskStatus.CANCEL.value, "concurrent cancellation marker", "doc"])
        return original(sql, params, *args, **kwargs)

    monkeypatch.setattr(services.database, "execute_sql", execute)
    services.tasks.update_progress("task-0", {"progress": -1, "progress_msg": "late failure"})
    doc = services.Document.get_by_id("doc")
    assert cancellation == [True]
    assert (doc.run, doc.progress_msg, doc.update_time) == (TaskStatus.CANCEL, "concurrent cancellation marker", 111)


@pytest.mark.parametrize("error", [pw.InterfaceError, pw.OperationalError])
@pytest.mark.parametrize("failures", [2, 3])
def test_only_document_write_retries_bounded_without_duplicate_task_logs(services, monkeypatch, error, failures):
    services.Document.update(run=TaskStatus.RUNNING).execute()
    original = services.database.execute_sql
    attempts = []

    def execute(sql, params=None, *args, **kwargs):
        if sql.startswith('UPDATE "document"'):
            attempts.append(sql)
            if len(attempts) <= failures:
                raise error("synthetic transient database failure")
        return original(sql, params, *args, **kwargs)

    monkeypatch.setattr(services.database, "execute_sql", execute)
    if failures == 3:
        with pytest.raises(error):
            services.tasks.update_progress("task-0", {"progress": -1, "progress_msg": "one synthetic append"})
    else:
        services.tasks.update_progress("task-0", {"progress": -1, "progress_msg": "one synthetic append"})
    assert len(attempts) == 3
    assert services.Task.get_by_id("task-0").progress_msg.count("one synthetic append") == 1
    doc = services.Document.get_by_id("doc")
    assert doc.run == (TaskStatus.RUNNING if failures == 3 else TaskStatus.FAIL)
    assert doc.update_time == (111 if failures == 3 else 222)


def test_missing_document_is_not_inserted(services):
    assert services.documents.fail_if_not_cancelled("missing", {"progress": -1}) == 0
    assert services.Document.select().count() == 1


def test_cancellation_between_document_retry_attempts_still_wins(services, monkeypatch):
    services.Document.update(run=TaskStatus.RUNNING).execute()
    original = services.database.execute_sql
    attempts = []

    def execute(sql, params=None, *args, **kwargs):
        if sql.startswith('UPDATE "document"'):
            attempts.append(sql)
            if len(attempts) == 1:
                original('UPDATE "document" SET "run" = ?, "progress_msg" = ? WHERE "id" = ?', [TaskStatus.CANCEL.value, "cancellation during retry", "doc"])
                raise pw.OperationalError("synthetic retryable failure")
        return original(sql, params, *args, **kwargs)

    monkeypatch.setattr(services.database, "execute_sql", execute)
    services.tasks.update_progress("task-0", {"progress": -1, "progress_msg": "one synthetic append"})
    assert len(attempts) == 2
    doc = services.Document.get_by_id("doc")
    assert (doc.run, doc.progress_msg, doc.update_time) == (TaskStatus.CANCEL, "cancellation during retry", 111)
    assert services.Task.get_by_id("task-0").progress_msg.count("one synthetic append") == 1
