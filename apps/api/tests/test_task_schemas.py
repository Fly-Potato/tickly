from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.tasks import (
    ParentOptionPageResponse,
    ParentOptionQuery,
    ParentOptionResponse,
    SortOrder,
    TaskCreateRequest,
    TaskDetailResponse,
    TaskGroupResponse,
    TaskListQuery,
    TaskListResponse,
    TaskPriority,
    TaskResponse,
    TaskSort,
    TaskStatus,
    TaskStatusFilter,
    TaskUpdateRequest,
    TopicListResponse,
)


class StoredTask:
    id = "00000000-0000-0000-0000-000000000001"
    user_id = "00000000-0000-0000-0000-000000000099"
    next_task_serial = 42
    serial = 18
    title = "调整布局"
    description = "调整 Todo List 布局"
    priority = "high"
    topic = "Tickly"
    status = "in_progress"
    due_at = datetime(2026, 8, 18, 8)
    completed_at = None
    parent_id = "00000000-0000-0000-0000-000000000002"
    created_at = datetime(2026, 8, 17, 8)
    updated_at = datetime(2026, 8, 17, 9)


def test_task_enums_expose_only_the_new_contract_values() -> None:
    assert [item.value for item in TaskPriority] == ["low", "medium", "high"]
    assert [item.value for item in TaskStatus] == [
        "new",
        "in_progress",
        "completed",
    ]
    assert [item.value for item in TaskStatusFilter] == [
        "all",
        "new",
        "in_progress",
        "completed",
    ]
    assert [item.value for item in TaskSort] == [
        "serial",
        "created_at",
        "due_at",
        "priority",
    ]


@pytest.mark.parametrize("description", [None, "", "   "])
def test_create_request_normalizes_required_fields_and_description_default(
    description: str | None,
) -> None:
    payload = TaskCreateRequest.model_validate(
        {
            "title": "  调整布局  ",
            "description": description,
            "topic": "  Tickly  ",
            "priority": None,
        }
    )

    assert payload.title == "调整布局"
    assert payload.description == "调整布局"
    assert payload.topic == "Tickly"
    assert payload.priority is None


def test_create_request_preserves_trimmed_description_and_normalizes_due_at() -> None:
    payload = TaskCreateRequest.model_validate(
        {
            "title": "任务",
            "description": "  单独的描述  ",
            "topic": "Tickly",
            "priority": "medium",
            "due_at": "2026-08-18T18:00:00+08:00",
            "parent_id": "00000000-0000-0000-0000-000000000001",
        }
    )

    assert payload.description == "单独的描述"
    assert payload.priority is TaskPriority.MEDIUM
    assert payload.due_at == datetime(2026, 8, 18, 10, tzinfo=UTC)
    assert payload.parent_id == "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "任务"},
        {"title": None, "topic": "Tickly"},
        {"title": "   ", "topic": "Tickly"},
        {"title": "a" * 201, "topic": "Tickly"},
        {"title": "任务", "topic": None},
        {"title": "任务", "topic": "   "},
        {"title": "任务", "topic": "a" * 101},
        {"title": "任务", "topic": "Tickly", "description": "a" * 4001},
        {"title": "任务", "topic": "Tickly", "priority": "urgent"},
        {"title": "任务", "topic": "Tickly", "parent_id": "a" * 37},
        {
            "title": "任务",
            "topic": "Tickly",
            "due_at": "2026-08-18T18:00:00",
        },
        {"title": "任务", "topic": "Tickly", "status": "new"},
    ],
)
def test_create_request_rejects_invalid_lengths_time_and_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TaskCreateRequest.model_validate(payload)


def test_update_request_trims_text_without_copying_title_to_description() -> None:
    payload = TaskUpdateRequest.model_validate(
        {
            "title": "  新标题  ",
            "description": "  独立描述  ",
            "topic": "  工作  ",
            "status": "completed",
        }
    )
    title_only = TaskUpdateRequest(title="  只更新标题  ")

    assert payload.title == "新标题"
    assert payload.description == "独立描述"
    assert payload.topic == "工作"
    assert payload.status is TaskStatus.COMPLETED
    assert payload.model_fields_set == {"title", "description", "topic", "status"}
    assert title_only.title == "只更新标题"
    assert title_only.description is None
    assert title_only.model_fields_set == {"title"}


@pytest.mark.parametrize("field", ["title", "description", "topic", "status"])
@pytest.mark.parametrize("value", [None, "   "])
def test_update_request_rejects_null_or_blank_required_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        TaskUpdateRequest.model_validate({field: value})


def test_update_request_allows_explicitly_clearing_nullable_fields() -> None:
    payload = TaskUpdateRequest.model_validate(
        {"priority": None, "due_at": None, "parent_id": None}
    )

    assert payload.priority is None
    assert payload.due_at is None
    assert payload.parent_id is None
    assert payload.model_fields_set == {"priority", "due_at", "parent_id"}


def test_update_request_normalizes_aware_due_at_and_rejects_naive_time() -> None:
    payload = TaskUpdateRequest.model_validate(
        {"due_at": "2026-08-18T18:00:00+08:00"}
    )

    assert payload.due_at == datetime(2026, 8, 18, 10, tzinfo=UTC)
    with pytest.raises(ValidationError):
        TaskUpdateRequest.model_validate({"due_at": "2026-08-18T18:00:00"})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "a" * 201},
        {"description": "a" * 4001},
        {"topic": "a" * 101},
        {"parent_id": "a" * 37},
        {"priority": "urgent"},
        {"status": "active"},
        {"completed_at": "2026-08-18T10:00:00Z"},
    ],
)
def test_update_request_rejects_empty_invalid_or_extra_patch(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TaskUpdateRequest.model_validate(payload)


def test_list_query_normalizes_topic_and_keeps_pagination_and_sorting() -> None:
    query = TaskListQuery.model_validate(
        {
            "status": "in_progress",
            "topic": "  Tickly  ",
            "sort": "serial",
            "order": "asc",
            "cursor": "opaque-cursor",
            "limit": 25,
        }
    )

    assert query.status is TaskStatusFilter.IN_PROGRESS
    assert query.topic == "Tickly"
    assert query.sort is TaskSort.SERIAL
    assert query.order is SortOrder.ASC
    assert query.cursor == "opaque-cursor"
    assert query.limit == 25


def test_list_query_has_stable_defaults_and_normalizes_blank_topic() -> None:
    query = TaskListQuery(topic="   ")

    assert query.status is TaskStatusFilter.ALL
    assert query.topic is None
    assert query.sort is TaskSort.CREATED_AT
    assert query.order is SortOrder.DESC
    assert query.cursor is None
    assert query.limit == 50


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "active"},
        {"topic": "a" * 101},
        {"sort": "title"},
        {"cursor": ""},
        {"cursor": "a" * 2049},
        {"limit": 0},
        {"limit": 101},
        {"unexpected": "value"},
    ],
)
def test_list_query_rejects_invalid_filters_and_bounds(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TaskListQuery.model_validate(payload)


def test_parent_option_query_normalizes_free_text_and_defaults() -> None:
    query = ParentOptionQuery(query="  #18  ")
    blank = ParentOptionQuery(query="   ")

    assert query.query == "#18"
    assert blank.query is None
    assert query.cursor is None
    assert query.limit == 50


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "a" * 201},
        {"cursor": ""},
        {"cursor": "a" * 2049},
        {"limit": 0},
        {"limit": 101},
        {"unexpected": "value"},
    ],
)
def test_parent_option_query_rejects_invalid_bounds_and_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ParentOptionQuery.model_validate(payload)


def test_task_response_reads_complete_stored_object_and_restores_utc() -> None:
    response = TaskResponse.model_validate(StoredTask())
    dumped = response.model_dump(mode="json")

    assert response.serial == 18
    assert response.description == "调整 Todo List 布局"
    assert response.priority is TaskPriority.HIGH
    assert response.topic == "Tickly"
    assert response.status is TaskStatus.IN_PROGRESS
    assert response.parent_id == "00000000-0000-0000-0000-000000000002"
    assert response.due_at == datetime(2026, 8, 18, 8, tzinfo=UTC)
    assert response.created_at.tzinfo is UTC
    assert dumped["created_at"].endswith("Z")
    assert "user_id" not in dumped
    assert "next_task_serial" not in dumped


def test_task_response_converts_aware_stored_datetime_to_utc() -> None:
    class AwareStoredTask(StoredTask):
        due_at = datetime(2026, 8, 18, 18, tzinfo=timezone(timedelta(hours=8)))

    response = TaskResponse.model_validate(AwareStoredTask())

    assert response.due_at == datetime(2026, 8, 18, 10, tzinfo=UTC)


def test_tree_detail_topic_and_parent_page_responses_have_strict_shapes() -> None:
    child = TaskResponse.model_validate(StoredTask())
    group = TaskGroupResponse(
        task=child,
        children=[child],
        child_count=2,
        completed_child_count=1,
        context_only=True,
    )
    page = TaskListResponse(items=[group], next_cursor="next-tree")
    detail = TaskDetailResponse.model_validate(
        {**child.model_dump(), "children": [child]}
    )
    topics = TopicListResponse(items=["Tickly", "工作"])
    parent = ParentOptionResponse.model_validate(StoredTask())
    parent_page = ParentOptionPageResponse(items=[parent], next_cursor="next-parent")

    assert page.items[0].task.serial == 18
    assert page.items[0].children[0].id == child.id
    assert page.items[0].child_count == 2
    assert page.items[0].completed_child_count == 1
    assert page.items[0].context_only is True
    assert detail.children == [child]
    assert topics.items == ["Tickly", "工作"]
    assert parent.model_dump(mode="json") == {
        "id": "00000000-0000-0000-0000-000000000001",
        "serial": 18,
        "title": "调整布局",
        "topic": "Tickly",
        "status": "in_progress",
    }
    assert parent_page.next_cursor == "next-parent"
    assert "user_id" not in page.model_dump_json()
    assert "next_task_serial" not in detail.model_dump_json()
