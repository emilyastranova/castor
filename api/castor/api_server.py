# main.py
import os
from fastapi import FastAPI
from dotenv import load_dotenv
from core.database.mongodb import setup_mongodb, get_database
from core.api.comments import router as comments_router

# Load environment variables from .env
load_dotenv()

# Create FastAPI instance
castor = FastAPI()

# Setup MongoDB connection
MONGODB_URI = os.getenv("MONGODB_URI")
setup_mongodb(MONGODB_URI)

# Include routers
castor.include_router(comments_router, prefix="/api", tags=["comments"])

# Dependency to inject MongoDB connection
def get_db():
    return get_database()

@castor.get("/")
async def read_root():
    return {"message": "Welcome to Castor API"}

if __name__ == "__main__":
    import uvicorn

    # Run the application with uvicorn
    uvicorn.run(castor, host="127.0.0.1", port=8000)
