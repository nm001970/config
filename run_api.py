import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(
    os.getenv(
        "PORT",
        os.getenv("API_PORT", "8000")
    ))

ACCESS_LOG = os.getenv("API_ACCESS_LOG", "false").lower() == "true"

if __name__ == "__main__":
    uvicorn.run(
        "subscription_api:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        access_log=ACCESS_LOG,
        log_level="warning"
    )