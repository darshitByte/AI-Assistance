"""Shared MongoDB client and collection handles."""
from pymongo import MongoClient

from core import config

_client = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=5000)
database = _client[config.MONGO_DB]

users = database["users"]
messages = database["messages"]
sessions = database["sessions"]
