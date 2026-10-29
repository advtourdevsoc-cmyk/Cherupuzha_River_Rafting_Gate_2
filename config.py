import os
from dotenv import load_dotenv

# Load local .env for development (ignored in production)
load_dotenv()


class Config:
	"""Application configuration from environment variables."""
	SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
	MONGO_URI = os.environ.get(
		"MONGO_URI",
		"mongodb://127.0.0.1:27017/raft_booking"  # fallback for local dev only
	)


# Backwards-compatible module-level names used elsewhere in the codebase
SECRET_KEY = Config.SECRET_KEY
MONGO_URI = Config.MONGO_URI

