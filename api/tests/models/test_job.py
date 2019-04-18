"""Test for creating a new Job object."""

from pydantic import ValidationError
from datetime import datetime
from castor.core.models.job import Job # pylint: disable=import-error

def test_create_job():
    """Test for creating a new Job object."""
    job = Job(
        task_id="123",
        name="Test Job",
        description="This is a test job.",
        tags=["Tag 1", "Tag 2"],
        command="Command 1",
        start_date=datetime.now().isoformat(),
        end_date=datetime.now().isoformat(),
        duration=60.0,
        created_by="Test User",
        type="Manual",
        depends_on=["Job 1", "Job 2"],
        status="To Do",
        logs={"stdin": "Input", "stdout": "Output", "stderr": "Error"},
        exit_code=0,
        hostname="localhost",
        username="testuser",
        environment_variables=[{"name": "Var 1", "value": "Value 1"}],
        artifacts=[{"name": "Artifact 1", "value": "Value 1"}],
        screenshots=["Screenshot 1", "Screenshot 2"]
    )
    assert job.task_id == "123"
    assert job.name == "Test Job"
    assert job.description == "This is a test job."
    assert job.tags == ["Tag 1", "Tag 2"]
    assert job.command == "Command 1"
    assert type(job.start_date) == datetime
    assert type(job.end_date) == datetime
    assert job.duration == 60
    assert job.created_by == "Test User"
    assert job.type == "Manual"
    assert job.depends_on == ["Job 1", "Job 2"]
    assert job.status == "To Do"
    assert job.logs == {"stdin": "Input", "stdout": "Output", "stderr": "Error"}
    assert job.exit_code == 0
    assert job.hostname == "localhost"
    assert job.username == "testuser"
    assert job.environment_variables == [{"name": "Var 1", "value": "Value 1"}]
    assert job.artifacts == [{"name": "Artifact 1", "value": "Value 1"}]
    assert job.screenshots == ["Screenshot 1", "Screenshot 2"]
    print(job.model_dump_json(indent=2)) # Use pytest -s to see debug output

def test_job_validation():
    """Intentionally create a Job object with invalid types."""
    try:
        Job(
            task_id="123",
            name="Test Job",
            description="This is a test job.",
            tags="Invalid tags", # Should be a list
            command="Command 1",
            start_date="2021-01-01T00:00:00Z", # Should parse to a datetime
            end_date="2021-01-01T00:01:00Z", # Should parse to a datetime
            duration=60,
            created_by="Test User",
            type="Manual",
            depends_on="Invalid depends on", # Should be a list
            status="To Do",
            logs="Invalid logs", # Should be a dictionary
            exit_code=0,
            hostname="localhost",
            username="testuser",
            environment_variables="Invalid environment variables", # Should be a list
            artifacts="Invalid artifacts", # Should be a list
            screenshots="Invalid screenshots" # Should be a list
        )
    except ValidationError as e:
        assert "tags" in str(e)
        assert "depends_on" in str(e)
        assert "logs" in str(e)
        assert "environment_variables" in str(e)
        assert "artifacts" in str(e)
        assert "screenshots" in str(e)
