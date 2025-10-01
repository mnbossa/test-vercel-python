import os

# Configuration from environment
WORKER_URL = os.environ.get("WORKER_URL")
SECRET = os.environ.get("SECRET")
MODEL = os.environ.get("MODEL", "HuggingFaceTB/SmolLM3-3B:hf-inference")