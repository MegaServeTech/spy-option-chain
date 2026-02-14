# configure.py
import os
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# Auto-detect environment
IS_CLOUD_RUN = os.getenv('K_SERVICE') is not None

# Default settings based on environment
if IS_CLOUD_RUN:
    # Production (Cloud Run): Use Cloud SQL Unix Socket
    # If DATABASE_URL is somehow missing or incomplete, this default ensures it works
    DEFAULT_URL = "mysql+pymysql://msdb:dbMega$3322@/spydata?unix_socket=/cloudsql/market-ana:asia-south1:msdb"
else:
    # Local Development: Use TCP localhost
    # Connects to your local Docker container or local MySQL instance
    DEFAULT_URL = "mysql+pymysql://msdb:dbMega$3322@127.0.0.1:3307/spydata"

# Get from env or use default
database_url = os.getenv('DATABASE_URL', DEFAULT_URL)

# FAILSAFE: If running in Cloud Run but URL looks like localhost (often happens by mistake), force the correct Cloud SQL path
if IS_CLOUD_RUN and ('127.0.0.1' in database_url or 'localhost' in database_url):
    print("⚠️  Correcting invalid localhost URL for Cloud Run...")
    database_url = "mysql+pymysql://msdb:dbMega$3322@/spydata?unix_socket=/cloudsql/market-ana:asia-south1:msdb"
elif IS_CLOUD_RUN and 'unix_socket' not in database_url:
     # Even if not localhost, ensure socket is present if it looks like a standard mysql URL
     if '@/' in database_url and 'cloudsql' not in database_url:
          database_url += "?unix_socket=/cloudsql/market-ana:asia-south1:msdb"

# Validate
if not database_url:
    raise ValueError("Missing required environment variable: DATABASE_URL")

# Mask password for printing
safe_print_url = database_url
if "@" in safe_print_url:
    try:
        part1 = safe_print_url.split("@")[0]
        part2 = safe_print_url.split("@")[1]
        user_pass = part1.split("://")[1]
        if ":" in user_pass:
            safe_print_url = safe_print_url.replace(user_pass.split(":")[1], "***")
    except:
        pass
print("database_url:", safe_print_url)

APP_CONFIG = {
    # CRITICAL: Cloud Run sets 'PORT'. We must use it or the container will fail to start.
    'PORT': int(os.getenv('PORT', 8080)),
    'HOST': os.getenv('FLASK_HOST', '0.0.0.0'),
    'DEBUG': os.getenv('FLASK_DEBUG', 'True').lower() == 'true',
    'SECRET_KEY': os.getenv('FLASK_SECRET_KEY', 'your-secure-secret-key-1234567890'),
    'DATABASE_URL': database_url
}
