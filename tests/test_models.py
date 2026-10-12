from app.db import models
from app.db.base import Base


def test_tables_are_registered():
    assert set(Base.metadata.tables) == {"users", "tasks"}


def test_task_belongs_to_user_and_cascades():
    fk = next(iter(Base.metadata.tables["tasks"].c.user_id.foreign_keys))

    assert fk.column.table.name == "users"
    assert fk.ondelete == "CASCADE"


def test_constraint_names_follow_convention():
    names = {c.name for c in Base.metadata.tables["tasks"].constraints}

    assert {
        "pk_tasks",
        "uq_tasks_user_id_task_no",
        "fk_tasks_user_id_users",
        "ck_tasks_one_deadline",
        "ck_tasks_priority_range",
        "ck_tasks_estimated_minutes_range",
        "ck_tasks_status_allowed",
    } <= names