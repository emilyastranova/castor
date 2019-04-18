"""Test for creating a new Comment object."""
from pydantic import ValidationError
from datetime import datetime
from castor.core.models.comment import Comment # pylint: disable=import-error

def test_create_comment():
    """Test for creating a new Comment object."""
    comment = Comment(
        project_id="123",
        task_id="456",
        timestamp=datetime.now(),
        user_id="789",
        content="This is a test comment."
    )
    assert comment.project_id == "123"
    assert comment.task_id == "456"
    assert isinstance(comment.timestamp, datetime)
    assert comment.user_id == "789"
    assert comment.content == "This is a test comment."
    print(comment.model_dump_json(indent=2)) # Use pytest -s to see debug output

def test_comment_validation():
    """Intentionally create a Comment object with invalid types."""
    try:
        Comment(
            project_id="123",
            task_id="456",
            timestamp=True, # Should be a datetime object
            user_id="789",
            content="This is a test comment."
        )
    except ValidationError as e:
        assert "timestamp" in str(e)
