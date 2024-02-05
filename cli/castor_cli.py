"""Connect to the MongoDB database and ask user for command to execute"""

import os
import sys
from pymongo import MongoClient, database
sys.path.insert(0, os.path.abspath("../api"))
from castor.core.models.job import Job #pylint: disable=import-error

def get_database():
    """Get the database"""
    client = MongoClient("mongodb://root:changeme@127.0.0.1:27017/")
    return client["castor"]


def send_command(db: database, command: str):
    """Send command to agent"""
    job_data = {
        "name": "CLI Command",
        "task_id": "CLI",
        "description": "Command sent from CLI",
        "command": "shell",
        "args": {"command": command},
    }
    job = Job(**job_data)
    result = db.jobs.insert_one(dict(job))
    # Add job to list using _id
    print(f"Job {result.inserted_id} created\n")


def command_loop(db: database):
    """Get command from user"""
    while True:
        command = input("castor@agent:~$ ")
        if command == "exit":
            break
        send_command(db, command)


def main():
    """Main loop"""
    # Get database
    db = get_database()
    print("Castor CLI\n----------")

    # Start command loop
    command_loop(db)


if __name__ == "__main__":
    main()
