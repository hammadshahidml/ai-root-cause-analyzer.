# Order Service Demo Microservice

A lightweight, robust microservice built with FastAPI, SQLAlchemy, and PostgreSQL. It features structured JSON logging, database startup resiliency, and runs inside Docker containers.

## Project Structure

```text
.
├── docker-compose.yml       # Orchestrates the order-service and postgres db
├── README.md                # This instructions file
└── order-service/
    ├── Dockerfile           # Builds the FastAPI container
    ├── main.py              # Main FastAPI application (under 150 lines)
    └── requirements.txt     # Python dependency definition
```

## Setup & Running

### 1. Configure Environment Variables
This project uses environment variable substitution to avoid hardcoding secrets. Create a `.env` file in the root directory:

```env
DB_USER=postgres
DB_PASSWORD=supersecurepassword
DB_NAME=postgres
```

### 2. Start the Stack
Build and run the containers using Docker Compose:

```bash
docker compose up --build
```

The database container has a healthcheck enabled, and the order-service will wait for it to be fully healthy before starting. Additionally, the Python application has a built-in startup retry mechanism to wait for the database connection.

## Testing the Endpoints

Once the service is running, it will be exposed on port `8000`.

### 1. Health Check
Checks service status and verifies the database connection:

**cURL (Bash):**
```bash
curl http://localhost:8000/health
```

**PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get
```

**Expected Response:**
```json
{"status": "ok"}
```

**Log Output (stdout JSON):**
```json
{"timestamp": "2026-07-10T13:45:00.123456Z", "level": "INFO", "service_name": "order-service", "endpoint": "/health", "status_code": 200, "message": "Request GET /health finished"}
```

---

### 2. Create Order
Saves a new order to the Postgres database:

**cURL (Bash):**
```bash
curl -X POST http://localhost:8000/order \
     -H "Content-Type: application/json" \
     -d '{"item": "Laptop", "quantity": 2}'
```

**PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/order" -Method Post -Body '{"item": "Laptop", "quantity": 2}' -Headers @{"Content-Type"="application/json"}
```

**Expected Response:**
```json
{"id": 1, "item": "Laptop", "quantity": 2}
```

**Log Output (stdout JSON):**
```json
{"timestamp": "2026-07-10T13:46:12.654321Z", "level": "INFO", "service_name": "order-service", "endpoint": "/order", "status_code": 201, "message": "Request POST /order finished"}
```
