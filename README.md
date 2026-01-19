# Option Index Database - Docker Deployment

A Flask-based web application for managing SPY index and options data with MySQL database.

## 📋 Prerequisites

- Docker Desktop installed ([Download here](https://www.docker.com/products/docker-desktop))
- Docker Compose (included with Docker Desktop)

## 🚀 Quick Start

### Using Docker Compose (Recommended)

1. **Start the entire stack** (MySQL + Flask app):
   ```bash
   docker-compose up -d
   ```

2. **Access the application**:
   - Open your browser and go to: http://localhost:5000

3. **Stop the stack**:
   ```bash
   docker-compose down
   ```

4. **Stop and remove all data** (including database):
   ```bash
   docker-compose down -v
   ```

### Building the Docker Image Manually

If you want to build just the application without MySQL:

```bash
docker build -t optionindexdb .
```

Run the container (make sure MySQL is available):
```bash
docker run -p 5000:5000 \
  -e MYSQL_HOST=your_mysql_host \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=Welcome123 \
  -e MYSQL_DB=spydata \
  optionindexdb
```

## 📁 Project Structure

```
.
├── Dockerfile              # Application container definition
├── docker-compose.yml      # Complete stack orchestration
├── requirements.txt        # Python dependencies
├── .dockerignore          # Files to exclude from build
├── app.py                 # Main Flask application
└── templates/             # HTML templates
```

## 🔧 Configuration

### Environment Variables

The application uses the following environment variables (configured in `docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MYSQL_USER` | `root` | MySQL username |
| `MYSQL_PASSWORD` | `Welcome123` | MySQL password |
| `MYSQL_HOST` | `localhost` | MySQL host (use `db` in Docker) |
| `MYSQL_DB` | `spydata` | Database name |
| `FLASK_ENV` | `production` | Flask environment |

### Ports

- **Flask Application**: `5000`
- **MySQL Database**: `3306`

## 📊 Features

- Upload and process CSV files for index and options data
- View stored data with preview tables
- Interactive options chain visualization
- ATM strike calculation
- Price and straddle charts with Plotly
- Date and time-based filtering

## 🛠️ Development

To run the application in development mode:

1. **Using Docker Compose**:
   ```bash
   docker-compose up
   ```
   The application will automatically reload on code changes if you mount the volume.

2. **Local Development** (without Docker):
   ```bash
   pip install -r requirements.txt
   python app.py
   ```

## 📝 Logs

View application logs:
```bash
docker-compose logs -f web
```

View MySQL logs:
```bash
docker-compose logs -f db
```

## 🔍 Troubleshooting

### Application can't connect to MySQL

1. Check if MySQL is ready:
   ```bash
   docker-compose logs db
   ```

2. Verify database health:
   ```bash
   docker-compose ps
   ```

3. Restart the services:
   ```bash
   docker-compose restart
   ```

### Reset everything

```bash
docker-compose down -v
docker-compose up -d
```

## 📦 Data Persistence

Data is persisted using Docker volumes:
- **mysql_data**: MySQL database files

## 🔒 Security Notes

- **Change default passwords** in production
- Use environment files (`.env`) for sensitive data
- Don't commit credentials to version control
- Consider using Docker secrets for production deployments

## 🤝 Contributing

1. Make changes to the code
2. Rebuild the Docker image:
   ```bash
   docker-compose up -d --build
   ```

## 📅 Backup

To backup the MySQL database:
```bash
docker exec optionindexdb_mysql mysqldump -uroot -pWelcome987 mst > backup.sql
```

To restore:
```bash
docker exec -i optionindexdb_mysql mysql -uroot -pWelcome987 mst < backup.sql
```
