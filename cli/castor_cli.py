"""Connect to the MongoDB database and ask user for command to execute"""

import os
from castor_lib.core.models.job import Job
from castor_lib.core.database import CastorDatabase, DatabaseConfig
from loguru import logger
from dotenv import load_dotenv

def send_command(db: CastorDatabase, command: str):
    """Send command to agent"""
    job_data = {
        "name": command,
        "task_id": "shell",
        "description": "Command sent from Castor CLI",
        "command": "shell",
        "args": {"command": command},
    }
    job = Job(**job_data)
    db.insert(Job, job)
    # Add job to list using _id
    logger.debug("Job created\n")


def command_loop(db: CastorDatabase):
    """Get command from user"""
    while True:
        command = input("castor@agent:~$ ")
        if command == "exit":
            break
        send_command(db, command)

@logger.catch
def main():
    """Main loop"""
    # Setup database connection
    load_dotenv()
    config = DatabaseConfig(
        host=os.getenv("CASTOR_SERVER_DB_HOST"),
        port=int(os.getenv("CASTOR_SERVER_DB_PORT")),
        username=os.getenv("CASTOR_SERVER_DB_USERNAME"),
        password=os.getenv("CASTOR_SERVER_DB_PASSWORD"),
    )
    db = CastorDatabase(config)
    print("Castor CLI\n----------")

    # Start command loop
    command_loop(db)


if __name__ == "__main__":
    main()
