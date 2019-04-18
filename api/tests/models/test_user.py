"""Test for creating a new Use object."""
from pydantic import ValidationError
from castor.core.models.user import User # pylint: disable=import-error

def test_create_user():
    """Test for creating a new User object."""
    user = User(
        username="testuser",
        email="user@example.com",
        first_name="Test",
        last_name="User",
        role="User",
        projects=["123", "456"],
        profile_picture="test.jpg",
        preferences=["Pref 1", "Pref 2"]
    )
    assert user.username == "testuser"
    assert user.email == "user@example.com"
    assert user.first_name == "Test"
    assert user.last_name == "User"
    assert user.role == "User"
    assert user.projects == ["123", "456"]
    assert user.profile_picture == "test.jpg"
    assert user.preferences == ["Pref 1", "Pref 2"]
    print(user.model_dump_json(indent=2)) # Use pytest -s to see debug output

def test_user_validation():
    """Intentionally create a User object with invalid types."""
    try:
        User(
            username="testuser",
            email=True, # Should be a string
            first_name="Test",
            last_name="User",
            role="User",
            projects=["123", "456"],
            profile_picture="test.jpg",
            preferences=["Pref 1", "Pref 2"]
        )
    except ValidationError as e:
        assert "email" in str(e)
