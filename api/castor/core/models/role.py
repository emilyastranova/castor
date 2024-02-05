"""Path: castor/core/models/role.py

This module contains the Pydantic models for the Role resource.

    - `_id`: ObjectId
    - `role_name`: String
    - `role_permissions`: Array of permission documents
"""

from typing import Optional
from pydantic import BaseModel

class RoleBase(BaseModel):
    """Base model for the Role resource.
    
    Note how _id is not included in the base model.
    This is because _id is generated by the database
    and should not be included when creating a new role.

    """
    role_name: str
    role_permissions: Optional[list] = []

class Role(RoleBase):
    """Model for the Role resource."""
    _id: str