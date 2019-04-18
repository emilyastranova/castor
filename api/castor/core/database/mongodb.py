# castor/core/database/mongodb.py
from core import Globals
from pymongo import MongoClient

def setup_mongodb(mongodb_uri: str):
    # Create MongoDB connection
    client = MongoClient(mongodb_uri)
    Globals.mongodb = client.get_database()

def get_database():
    if Globals.mongodb is None:
        raise ValueError("MongoDB is not set up. Call setup_mongodb first.")
    return Globals.mongodb
