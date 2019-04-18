"""Connect to the MongoDB database and ask user for command to execute"""

from pymongo import MongoClient, database


def get_database():
    """Get the database"""
    client = MongoClient("mongodb://root:changeme@127.0.0.1:27017/")
    return client["castor"]


def send_command(db: database, command: str):
    """Send command to agent"""
    job = {
        "command": command,
        "status": "pending",
    }
    result = db.jobs.insert_one(job)
    # Add job to list using _id
    job["_id"] = result.inserted_id
    print(f"Job {job['_id']} created\n")


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
