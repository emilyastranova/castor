# Database Schema

## Collections

1. **Clients Collection:**

   - `_id`: ObjectId
   - `client_name`: String
   - `client_contacts`: Array of contact Objects
     - `name`: String
     - `email`: String
     - `phone`: String
   - `client_notes`: String
   - `client_logo`: String or Binary data
   - `projects`: Array of project IDs

2. **Projects Collection:**

   - `_id`: ObjectId
   - `codename`: String (e.g. MPR23-101)
   - `project_name`: String
   - `client_id`: ObjectId (reference to Clients Collection)
   - `start_date`: Date
   - `end_date`: Date
   - `type`: String (e.g. "External", "Red Team", "Purple Team", "Custom")
   - `tasks`: Array of task IDs
   - `users`: Array of user IDs (reference to Users Collection)
   - `notes`: String
   - `comments`: Array of comment documents
   - `screenshots`: Array of screenshot documents
   - `attachments`: Array of attachment documents
   - `budget`: Number
   - `expenses`: Array of expense documents
   - `activity_logs`: Array of activity log documents
   - `secrets`: Array of secrets documents
   - `created_by`: User ID (reference to Users Collection)
   - `bookmarks`: Array of URL objects
     - `name`: String
     - `url`: String

3. **Tasks Collection:**

   - `_id`: ObjectId
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

4. **Jobs Collection:**

   - `_id`: ObjectId
   - `task_id`: ObjectId (reference to Tasks Collection)
   - `name`: String
   - `description`: String
   - `tags`: Array of tag IDs (reference to Tags Collection)
   - `args`: Object document
     - `arg_name`: `arg_value` (e.g., `cidr`, `192.168.0.0/24`)
   - `command`: ObjectId (reference to Commands Collection)
   - `start_date`: Date
   - `end_date`: Date
   - `duration`: Number (in seconds)
   - `created_by`: User ID (reference to Users Collection)
   - `type`: String (e.g., "Manual", "Automated")
   - `depends_on`: Array of job IDs
   - `status`: String (e.g., "To Do," "In Progress," "Completed")
   - `logs`: Object document
     - `stdin`: String
     - `stdout`: String
     - `stderr`: String
   - `exit_code`: Number
   - `agent_id`: ObjectId (reference to Agents Collection)
   - `hostname`: String
   - `username`: String
   - `environment_variables`: Array of object documents
     - `name`: String
     - `value`: String
   - `artifacts`: Array of object documents
     - `name`: String
     - `value`: String
   - `screenshots`: Array of screenshot documents

5. **Screenshots Collection:**

   - `_id`: ObjectId
   - `project_id`: String (reference to Projects Collection)
   - `task_id`: ObjectId (reference to Tasks Collection)
   - `job_id`: ObjectId (reference to Jobs Collection)
   - `timestamp`: Date
   - `content`: String or Binary data

6. **Secrets Collection:**

   - `_id`: ObjectId
   - `project_id`: String (reference to Projects Collection)
   - `name`: String
   - `type`: String (e.g. "Password", "API Key", "SSH Key", "Certificate")
   - `shared`: Boolean (e.g., true if shared with other users)
   - `username`: String
   - `password`: String
   - `public_key`: String
   - `private_key`: String
   - `certificate`: String
   - `url`: String
   - `notes`: String

7. **Comments Collection:**

   - `_id`: ObjectId
   - `project_id`: String (reference to Projects Collection)
   - `task_id`: ObjectId (reference to Tasks Collection)
   - `timestamp`: Date
   - `user_id`: ObjectId (reference to Users Collection)
   - `content`: String

8. **Expenses Collection:**

   - `_id`: ObjectId
   - `project_id`: String (reference to Projects Collection)
   - `name`: String
   - `amount`: Number
   - `date`: Date
   - `notes`: String

9. **Activity Logs Collection:**

   - `_id`: ObjectId
   - `project_id`: String (reference to Projects Collection)
   - `timestamp`: Date
   - `content`: String

10. **Attachments Collection:**

    - `_id`: ObjectId
    - `project_id`: String (reference to Projects Collection)
    - `name`: String
    - `file_name`: String
    - `timestamp`: Date
    - `file_url`: String or Binary data
    - `uploaded_by`: User ID (reference to Users Collection)
    - `notes`: String

11. **Users Collection:**

    - `_id`: ObjectId
    - `username`: String
    - `email`: String
    - `first_name`: String
    - `last_name`: String
    - `role`: String (e.g., "Admin", "Manager", "User")
    - `projects`: Array of project IDs
    - `profile_picture`: String or Binary data
    - `preferences`: Array of preference documents

12. **Preferences Collection:**

    - `_id`: ObjectId
    - `user_id`: ObjectId (reference to Users Collection)
    - `theme`: String (e.g., "Dark", "Light")
    - `language`: String (e.g., "English", "Spanish")
    - `timezone`: String (e.g., "America/New_York", "America/Los_Angeles")
    - `notifications`: Boolean
    - `notification_sound`: Boolean
    - `notification_sound_data`: String or Binary data

13. **Roles Collection:**

    - `_id`: ObjectId
    - `role_name`: String
    - `role_permissions`: Array of permission documents

14. **Permissions Collection:**

    - `_id`: ObjectId
    - `permission_name`: String
    - `permission_description`: String

15. **Tags Collection:**

    - `_id`: ObjectId
    - `tag_name`: String
    - `tag_description`: String
    - `tag_color`: String (e.g., "#000000", "#FFFFFF")
    - `user_added`: Boolean
    - `user_id`: ObjectId (reference to Users Collection)

16. **Notifications Collection:**
    - `_id`: ObjectId
    - `project_id`: String (reference to Projects Collection)
    - `notification_title`: String
    - `notification_content`: String
    - `notification_timestamp`: Date
    - `notification_read`: Boolean

17. **Commands Collection:**
      - `_id`: ObjectId
      - `id`: String (e.g., "nmap")
      - `schema_version`: Number
      - `description`: String
      - `created_by`: String (not a user in the database, this is for the YAML file version control)
      - `checks`: Array of check documents (reference to Checks Collection)
      - `tags`: Array of tag IDs (reference to Tags Collection)
      - `args`: Array of argument documents
      - `command`: String

18. **Checks Collection:**
      - `_id`: ObjectId
      - `id`: String (e.g., "is_nmap_installed")
      - `description`: String
      - `created_by`: String (not a user in the database, this is for the YAML file version control)
      - `tags`: Array of tag IDs (reference to Tags Collection)
      - `command`: String
      - `success_criteria`:
         - `type`: String (e.g., "regex", "string", "boolean", "exit_code")
         - `value`: String

19. **Agents Collection:**
      - `_id`: ObjectId
      - `agent_name`: String
      - `agent_type`: String (e.g., "Windows", "Linux", "Mac", "Docker", "Kubernetes")
      - `agent_status`: String (e.g., "Online", "Offline", "Busy", "Idle")
      - `agent_logs`: Array of log documents
      - `agent_jobs`: Array of job documents
      - `agent_tags`: Array of tag documents
      - `agent_preferences`: Array of preference documents
      - `agent_activity_logs`: Array of activity log documents
      - `agent_secrets`: Array of secrets documents

## Indexes

1. **Brainstorming**
   - Create indexes based on the fields frequently queried to enhance performance.
   - For example, we may want to create indexes on `project_id` in the Tasks Collection, `task_id` in Logs, Screenshots, and Comments Collections, etc.

## References

1. [MXFlow](https://github.com/metaory/mxflow-cli) - Inspired the command schema.
