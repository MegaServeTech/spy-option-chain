import os
from dotenv import load_dotenv

# Load .env variables (commented out for production)
# load_dotenv()

# Database Configuration
# Priority: DATABASE_URL from environment, or build from individual vars with local defaults
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    # Fallback: Build DATABASE_URL from individual components (for local development)
    MYSQL_USER = os.getenv('MYSQL_USER', 'msdb')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', 'dbMega$3322')
    MYSQL_HOST = os.getenv('MYSQL_HOST', '127.0.0.1')
    MYSQL_PORT = os.getenv('MYSQL_PORT', '3307')
    MYSQL_DB = os.getenv('MYSQL_DB', 'spydata')
    DATABASE_URL = f'mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}'
    print(f"ℹ️ Using local database config: {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
else:
    print(f"ℹ️ Using DATABASE_URL from environment")

print("database_url:", DATABASE_URL.replace(DATABASE_URL.split('@')[0].split('://')[1], '***'))

# Application Configuration
APP_CONFIG = {
    'PORT': int(os.getenv('PORT', os.getenv('FLASK_PORT', 8080))),
    'HOST': os.getenv('FLASK_HOST', '0.0.0.0'),
    'DEBUG': os.getenv('FLASK_DEBUG', 'False').lower() == 'true',
    'SECRET_KEY': os.getenv('FLASK_SECRET_KEY', 'spy-option-chain-secret-key-2024'),
    'DATABASE_URL': DATABASE_URL
}
