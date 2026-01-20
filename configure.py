import os
import sys

# Try to load dotenv if available (only for local development)
try:
    from dotenv import load_dotenv
    # Uncomment below for local development with .env file
    # load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, use environment variables directly

# ═══════════════════════════════════════════════════════════════════
#                    PRODUCTION ENVIRONMENT DETECTION
# ═══════════════════════════════════════════════════════════════════

# Detect if running in Cloud Run (more reliable than checking K_SERVICE alone)
IS_CLOUD_RUN = os.getenv('K_SERVICE') is not None
IS_LOCAL_DEV = not IS_CLOUD_RUN

print("=" * 70)
print(f"🌍 Environment: {'CLOUD RUN (Production)' if IS_CLOUD_RUN else 'LOCAL DEVELOPMENT'}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════
#                    DATABASE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

DATABASE_URL = os.getenv('DATABASE_URL')

if IS_CLOUD_RUN and not DATABASE_URL:
    # In production (Cloud Run), DATABASE_URL is REQUIRED
    error_msg = """
    ╔════════════════════════════════════════════════════════════════╗
    ║              ❌ CRITICAL CONFIGURATION ERROR ❌                ║
    ╠════════════════════════════════════════════════════════════════╣
    ║  DATABASE_URL environment variable is NOT SET in Cloud Run!   ║
    ║                                                                ║
    ║  For Cloud SQL (Unix Socket - RECOMMENDED):                   ║
    ║  DATABASE_URL=mysql+pymysql://USER:PASS@/DB?unix_socket=/cloudsql/PROJECT:REGION:INSTANCE
    ║                                                                ║
    ║  For Cloud SQL (TCP):                                         ║
    ║  DATABASE_URL=mysql+pymysql://USER:PASS@CLOUD_SQL_IP:3306/DB  ║
    ║                                                                ║
    ║  Set this in Cloud Run:                                       ║
    ║  gcloud run services update SERVICE_NAME --set-env-vars       ║
    ║    DATABASE_URL="your-connection-string"                      ║
    ╚════════════════════════════════════════════════════════════════╝
    """
    print(error_msg, file=sys.stderr)
    # Use a dummy URL to allow app to start (will fail on first DB query)
    DATABASE_URL = "mysql+pymysql://dummy:dummy@localhost:3306/dummy"
    print("⚠️  Using dummy DATABASE_URL - app will fail on first database query!", file=sys.stderr)

elif not DATABASE_URL:
    # Local development: Build from individual environment variables or use defaults
    MYSQL_USER = os.getenv('MYSQL_USER', 'msdb')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', 'dbMega$3322')
    MYSQL_HOST = os.getenv('MYSQL_HOST', '127.0.0.1')
    MYSQL_PORT = os.getenv('MYSQL_PORT', '3307')
    MYSQL_DB = os.getenv('MYSQL_DB', 'spydata')
    
    DATABASE_URL = f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}'
    print(f"📍 Local Database: {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
else:
    # DATABASE_URL is set (production or explicit local config)
    if IS_CLOUD_RUN:
        print("✅ DATABASE_URL configured for Cloud Run")
        # Check if using Cloud SQL Unix socket (recommended)
        if 'unix_socket=/cloudsql/' in DATABASE_URL:
            print("✅ Using Cloud SQL Unix Socket (Recommended)")
        else:
            print("⚠️  Using TCP connection (Unix Socket recommended for Cloud SQL)")
    else:
        print("✅ DATABASE_URL configured for local development")

# Masked URL for logging (hide credentials)
try:
    masked_url = DATABASE_URL.replace(
        DATABASE_URL.split('@')[0].split('://')[1],
        '***:***'
    )
    print(f"🔗 Connection: {masked_url}")
except Exception:
    print(f"🔗 Connection: [unable to parse URL]")

print("=" * 70)

# ═══════════════════════════════════════════════════════════════════
#                    APPLICATION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

APP_CONFIG = {
    'PORT': int(os.getenv('PORT', 8080)),  # Cloud Run sets PORT=8080
    'HOST': '0.0.0.0',  # Always bind to 0.0.0.0 in containers
    'DEBUG': os.getenv('FLASK_DEBUG', 'False').lower() == 'true',
    'SECRET_KEY': os.getenv('FLASK_SECRET_KEY', 'spy-option-chain-secret-key-2024'),
    'DATABASE_URL': DATABASE_URL,
    'IS_PRODUCTION': IS_CLOUD_RUN
}

# Security: Never enable debug in production
if IS_CLOUD_RUN and APP_CONFIG['DEBUG']:
    print("⚠️  WARNING: Debug mode is enabled in production! Setting to False for security.")
    APP_CONFIG['DEBUG'] = False

print(f"🚀 App Config: Port={APP_CONFIG['PORT']}, Host={APP_CONFIG['HOST']}, Debug={APP_CONFIG['DEBUG']}")
print("=" * 70)
