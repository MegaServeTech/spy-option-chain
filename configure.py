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

if not DATABASE_URL:
    # Use the user-provided URL directly as the default
    DATABASE_URL = "mysql+pymysql://msdb:dbMega$3322@127.0.0.1:3307/spydata"
    print(f"⚠️  DATABASE_URL not set. Using hardcoded default: {DATABASE_URL}")
else:
    print("✅ Using configured DATABASE_URL")

# Check if using Cloud SQL Unix socket (recommended for Cloud Run)
if IS_CLOUD_RUN and 'unix_socket' in DATABASE_URL:
    print("✅ Using Cloud SQL Unix Socket")

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
