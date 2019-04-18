#!/usr/bin/env python3
"""Script to connect to MongoDB and read jobs collection."""
from pymongo import MongoClient
from castor.core.models.job import Job # pylint: disable=import-error

# Connect to MongoDB
client = MongoClient("mongodb://root:changeme@localhost:27017/")
db = client["castor"]
collection = db["jobs"]

# Read all jobs from the collection
jobs = collection.find()
for job in jobs:
    print(Job(**job).model_dump_json(indent=2))
client.close()
