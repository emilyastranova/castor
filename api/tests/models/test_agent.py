"""Test for creating a new Agent object."""
from pydantic import ValidationError
from castor.core.models.agent import Agent # pylint: disable=import-error

def test_create_agent():
    """Test for creating a new Agent object."""
    agent = Agent(
        name="Test Agent",
        type="Windows",
        status="Online",
        logs=["Log 1", "Log 2"],
        jobs=["Job 1", "Job 2"],
        agent_tags=["Tag 1", "Tag 2"],
        agent_preferences=["Pref 1", "Pref 2"],
        activity_logs=["Activity Log 1", "Activity Log 2"],
        secrets=["Secret 1", "Secret 2"]
    )
    assert agent.name == "Test Agent"
    assert agent.type == "Windows"
    assert agent.status == "Online"
    assert agent.logs == ["Log 1", "Log 2"]
    assert agent.jobs == ["Job 1", "Job 2"]
    assert agent.agent_tags == ["Tag 1", "Tag 2"]
    assert agent.agent_preferences == ["Pref 1", "Pref 2"]
    assert agent.activity_logs == ["Activity Log 1", "Activity Log 2"]
    assert agent.secrets == ["Secret 1", "Secret 2"]
    print(agent.model_dump_json(indent=2)) # Use pytest -s to see debug output

def test_agent_validation():
    """Intentionally create an Agent object with invalid types."""
    try:
        Agent(
            name="Test Agent",
            type="Windows",
            status="Online",
            logs="Invalid logs", # Should be a list
            jobs="Invalid jobs", # Should be a list
            agent_tags="Invalid tags", # Should be a list
            agent_preferences="Invalid preferences", # Should be a list
            activity_logs="Invalid activity logs", # Should be a list
            secrets="Invalid secrets" # Should be a list
        )
    except ValidationError as e:
        assert "logs" in str(e)
        assert "jobs" in str(e)
        assert "agent_tags" in str(e)
        assert "agent_preferences" in str(e)
        assert "activity_logs" in str(e)
        assert "secrets" in str(e)
