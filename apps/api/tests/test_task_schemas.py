from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.tasks import (
    SortOrder,
    TaskCreateRequest,
    TaskListQuery,
    TaskPriority,
    TaskResponse,
    TaskSort,
    TaskStatus,
    TaskUpdateRequest,
)


def test_create_request_normalizes_title_due_at_and_defaults() -> None:
    payload = TaskCreateRequest.model_validate(
        {
            "title": "  完成 Todo API  ",
            "due_at": "2026-07-30T18:00:00+08:00",
        }
    )

    assert payload.title == "完成 Todo API"
    assert payload.notes is None
    assert payload.priority is TaskPriority.NONE
    assert payload.due_at == datetime(2026, 7, 30, 10, tzinfo=UTC)


@pytest.mark.parametrize("notes", [None, "", "  第一行\n第二行  ", "a" * 4000])
def test_create_request_preserves_nullable_notes_and_boundary(
    notes: str | None,
) -> None:
    payload = TaskCreateRequest(title="任务", notes=notes)

    assert payload.notes == notes


@pytest.mark.parametrize(
    "payload",
    [
        {"title": None},
        {"title": "   "},
        {"title": "a" * 201},
        {"title": "ok", "notes": "a" * 4001},
        {"title": "ok", "priority": "urgent"},
        {"title": "ok", "due_at": "2026-07-30T18:00:00"},
        {"title": "ok", "is_completed": True},
    ],
)
def test_create_request_rejects_invalid_or_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TaskCreateRequest.model_validate(payload)


def test_update_request_distinguishes_omitted_and_explicit_null() -> None:
    clear_notes = TaskUpdateRequest(notes=None)

    assert clear_notes.model_fields_set == {"notes"}
    assert TaskUpdateRequest(title="  新标题  ").title == "新标题"
    with pytest.raises(ValidationError):
        TaskUpdateRequest()
    for field in ("title", "priority", "is_completed"):
        with pytest.raises(ValidationError):
            TaskUpdateRequest.model_validate({field: None})
    with pytest.raises(ValidationError):
        TaskUpdateRequest.model_validate(
            {"notes": None, "completed_at": "2026-07-30T10:00:00Z"}
        )


def test_update_request_normalizes_aware_due_at_and_rejects_naive_time() -> None:
    payload = TaskUpdateRequest.model_validate(
        {"due_at": "2026-07-30T18:00:00+08:00"}
    )

    assert payload.due_at == datetime(2026, 7, 30, 10, tzinfo=UTC)
    with pytest.raises(ValidationError):
        TaskUpdateRequest.model_validate({"due_at": "2026-07-30T18:00:00"})


def test_list_query_has_stable_defaults_bounds_and_forbids_extra_fields() -> None:
    query = TaskListQuery()

    assert query.status is TaskStatus.ALL
    assert query.sort is TaskSort.CREATED_AT
    assert query.order is SortOrder.DESC
    assert query.limit == 50
    with pytest.raises(ValidationError):
        TaskListQuery(limit=0)
    with pytest.raises(ValidationError):
        TaskListQuery(limit=101)
    with pytest.raises(ValidationError):
        TaskListQuery.model_validate({"unexpected": "value"})


def test_task_response_reads_orm_attributes_and_restores_utc() -> None:
    class StoredTask:
        id = "00000000-0000-0000-0000-000000000001"
        title = "任务"
        notes = None
        is_completed = False
        priority = "none"
        due_at = None
        completed_at = None
        created_at = datetime(2026, 7, 28, 8)
        updated_at = datetime(2026, 7, 28, 8)

    response = TaskResponse.model_validate(StoredTask())
    dumped = response.model_dump(mode="json")

    assert response.created_at.tzinfo is UTC
    assert dumped["created_at"].endswith("Z")
    assert "user_id" not in dumped
