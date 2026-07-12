import logging
import os
import time
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pythonjsonlogger import jsonlogger

# 1. Logging Configuration
class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        log_record["level"] = record.levelname
        log_record["service_name"] = "order-service"
        log_record["endpoint"] = log_record.get("endpoint", None)
        log_record["status_code"] = log_record.get("status_code", None)

stdout_handler = logging.StreamHandler()
formatter = CustomJsonFormatter(
    "%(timestamp)s %(level)s %(service_name)s %(endpoint)s %(status_code)s %(message)s"
)
stdout_handler.setFormatter(formatter)

for logger_name in [None, "uvicorn", "uvicorn.error"]:
    l = logging.getLogger(logger_name)
    l.setLevel(logging.INFO)
    for h in list(l.handlers):
        l.removeHandler(h)
    l.addHandler(stdout_handler)

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logger = logging.getLogger("order-service")

# 2. Database Configuration & Initialization
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = None
for i in range(15):
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Successfully connected to the database.")
        break
    except Exception as e:
        if i == 14:
            logger.error("Could not connect to database after 15 retries. Exiting.")
            raise e
        logger.info(f"Database not ready. Retrying in 2 seconds... ({i+1}/15)")
        time.sleep(2)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    item = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 3. FastAPI App & Middleware
app = FastAPI()

class OrderCreate(BaseModel):
    item: str
    quantity: int

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    try:
        response = await call_next(request)
        status_code = response.status_code
        level = logging.INFO if status_code < 400 else logging.WARNING
        logger.log(
            level,
            f"Request {request.method} {request.url.path} finished",
            extra={"endpoint": request.url.path, "status_code": status_code}
        )
        return response
    except Exception as exc:
        logger.error(
            f"Request {request.method} {request.url.path} failed: {str(exc)}",
            extra={"endpoint": request.url.path, "status_code": 500}
        )
        raise exc

# 4. Endpoints
@app.post("/order", status_code=201)
def create_order(order_data: OrderCreate, db: Session = Depends(get_db)):
    db_order = Order(item=order_data.item, quantity=order_data.quantity)
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
    return {"id": db_order.id, "item": db_order.item, "quantity": db_order.quantity}

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        logger.error(
            f"Health check failed: {str(e)}",
            extra={"endpoint": "/health", "status_code": 500}
        )
        raise HTTPException(status_code=500, detail="Database connection failure")

# ============================================================
# FOR FAILURE TESTING ONLY - remove before production
# ============================================================

# Module-level list that grows and is never cleared, to simulate a
# memory leak when this endpoint is called repeatedly.
_leak_storage = []

@app.post("/debug/leak")
def debug_leak():
    """FOR FAILURE TESTING ONLY. Allocates ~10MB and never releases it,
    simulating a memory leak over repeated calls."""
    chunk = bytearray(10 * 1024 * 1024)  # 10MB
    _leak_storage.append(chunk)
    logger.warning(
        f"Debug leak endpoint called. Total chunks held: {len(_leak_storage)} "
        f"(~{len(_leak_storage) * 10}MB allocated)",
        extra={"endpoint": "/debug/leak", "status_code": 200}
    )
    return {"chunks_held": len(_leak_storage), "approx_mb": len(_leak_storage) * 10}

@app.post("/debug/slow-query")
def debug_slow_query(db: Session = Depends(get_db)):
    """FOR FAILURE TESTING ONLY. Runs an artificially slow DB query
    (pg_sleep) to simulate high latency / a slow database connection."""
    try:
        db.execute(text("SELECT pg_sleep(10)"))
        logger.warning(
            "Debug slow-query endpoint completed after 10s delay",
            extra={"endpoint": "/debug/slow-query", "status_code": 200}
        )
        return {"status": "completed", "delay_seconds": 10}
    except Exception as e:
        logger.error(
            f"Slow query failed: {str(e)}",
            extra={"endpoint": "/debug/slow-query", "status_code": 500}
        )
        raise HTTPException(status_code=500, detail="Slow query failed")