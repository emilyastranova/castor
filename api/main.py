#!/usr/bin/env python3
"""Script to connect to MongoDB and put a JSON job in jobs collection."""
import json
from pymongo import MongoClient
from castor.core.models.job import Job # pylint: disable=import-error

# Connect to MongoDB
client = MongoClient("mongodb://root:changeme@localhost:27017/")
db = client["castor"]
collection = db["jobs"]

# Get job from ../examples/job.json
with open("../database/examples/job.json", "r") as file:
    job = json.load(file)

# Create Job object
job = Job(**job)

# Insert job in MongoDB
collection.insert_one(dict(job))
client.close()