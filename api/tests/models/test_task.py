"""Test for creating a new Task object.

    - `project_id`: String (reference to Projects Collection)
    - `title`: String
    - `description`: String
    - `created_at`: Date
    - `updated_at`: Date
    - `created_by`: User ID (reference to Users Collection)
    - `type`: String (e.g., "Manual", "Automated")
    - `depends_on`: Array of task IDs
    - `tasks`: Array of task documents
    - `parent_task`: Task ID (reference to Tasks Collection)
    - `assigned_to`: Array of user IDs (reference to Users Collection)
    - `tags`: Array of tag IDs (reference to Tags Collection)
    - `priority`: Number (e.g., 0 for none, 1 for low, 2 for medium, 3 for high)
    - `jobs`: Array of job documents
    - `status`: String (e.g., "To Do," "In Progress," "Completed")
    - `comments`: Array of comment documents
    - `screenshots`: Array of screenshot documents

"""

from pydantic import ValidationError
from castor.core.models.task import Task # pylint: disable=import-error

def test_create_task():
    """Test for creating a new Task object."""
    task = Task(
        project_id="123",
        title="Test Task",
        description="This is a test task.",
        created_at="2021-01-01T00:00:00Z",
        updated_at="2021-01-01T00:01:00Z",
        created_by="Test User",
        type="Manual",
        depends_on=["Task 1", "Task 2"],
        tasks=["Subtask 1", "Subtask 2"],
        parent_task="Parent Task",
        assigned_to=["User 1", "User 2"],
        tags=["Tag 1", "Tag 2"],
        priority=1,
        jobs=["Job 1", "Job 2"],
        status="To Do",
        comments=["Comment 1", "Comment 2"],
        screenshots=["Screenshot 1", "Screenshot 2"]
    )
    assert task.project_id == "123"
    assert task.title == "Test Task"
    assert task.description == "This is a test task."
    assert task.created_at == "2021-01-01T00:00:00Z"
    assert task.updated_at == "2021-01-01T00:01:00Z"
    assert task.created_by == "Test User"
    assert task.type == "Manual"
    assert task.depends_on == ["Task 1", "Task 2"]
    assert task.tasks == ["Subtask 1", "Subtask 2"]
    assert task.parent_task == "Parent Task"
    assert task.assigned_to == ["User 1", "User 2"]
    assert task.tags == ["Tag 1", "Tag 2"]
    assert task.priority == 1
    assert task.jobs == ["Job 1", "Job 2"]
    assert task.status == "To Do"
    assert task.comments == ["Comment 1", "Comment 2"]
    assert task.screenshots == ["Screenshot 1", "Screenshot 2"]
    print(task.model_dump_json(indent=2)) # Use pytest -s to see debug output

def test_task_validation():
    """Intentionally create a Task object with invalid types."""
    try:
        Task(
            project_id="123",
            title="Test Task",
            description="This is a test task.",
            created_at="2021-01-01T00:00:00Z",
            updated_at="2021-01-01T00:01:00Z",
            created_by="Test User",
            type="Manual",
            depends_on="Invalid depends on", # Should be a list
            tasks="Invalid tasks", # Should be a list
            parent_task=["Invalid parent task"], # Should be a string
            assigned_to="Invalid assigned to", # Should be a list
            tags="Invalid tags", # Should be a list
            priority="Invalid priority", # Should be an int
            jobs="Invalid jobs", # Should be a list
            status=["Invalid status"], # Should be a string
            comments="Invalid comments", # Should be a list
            screenshots="Invalid screenshots" # Should be a list
        )
    except ValidationError as e:
        assert "depends_on" in str(e)
        assert "tasks" in str(e)
        assert "parent_task" in str(e)
        assert "assigned_to" in str(e)
        assert "tags" in str(e)
        assert "priority" in str(e)
        assert "jobs" in str(e)
        assert "status" in str(e)
        assert "comments" in str(e)
        assert "screenshots" in str(e)
