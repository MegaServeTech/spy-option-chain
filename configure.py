# configure.py
import os
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# Use the specific connection string provided by the user
# Updated database name from 'mst' to 'spydata' as requested
# This Unix Socket connection string is specifically for Cloud Run
DEFAULT_URL = "mysql+pymysql://msdb:dbMega$3322@/spydata?unix_socket=/cloudsql/market-ana:asia-south1:msdb"

# Use environment variable if set, otherwise use the default provided string
database_url = os.getenv('DATABASE_URL', DEFAULT_URL)

# Validate required environment variable
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
