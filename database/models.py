import os
import datetime
import json
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class Camera(Base):
    __tablename__ = 'cameras'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    location_name = Column(String(255), default="City Intersection")
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    direction = Column(String(50), default='North → South')
    status = Column(String(50), default='ACTIVE')  # ACTIVE, MAINTENANCE, OFFLINE
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    events = relationship("PlateEvent", back_populates="camera", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "location_name": self.location_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "direction": self.direction or "North → South",
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class PlateEvent(Base):
    __tablename__ = 'plate_events'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(Integer, ForeignKey('cameras.id'), nullable=False, index=True)
    plate_text = Column(String(20), nullable=False, index=True)
    confidence = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    vehicle_type = Column(String(50), default='Car')  # Car, Truck, Bus, Motorcycle
    vehicle_color = Column(String(50), default='White')
    make_model = Column(String(100), default='Sedan')
    direction = Column(String(50), default='Northbound')
    speed_estimate_kmh = Column(Float, nullable=True)
    is_provisional = Column(Integer, default=0)  # 1 if occluded/low-confidence fallback
    provisional_id = Column(String(50), nullable=True)
    resolved_plate = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    camera = relationship("Camera", back_populates="events")

    def to_dict(self):
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "camera_name": self.camera.name if self.camera else None,
            "camera_location": self.camera.location_name if self.camera else None,
            "latitude": self.camera.latitude if self.camera else None,
            "longitude": self.camera.longitude if self.camera else None,
            "plate_text": self.plate_text,
            "confidence": round(self.confidence, 3),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "direction": self.direction,
            "vehicle_type": self.vehicle_type,
            "vehicle_color": self.vehicle_color or "White",
            "make_model": self.make_model or "Sedan",
            "speed_estimate_kmh": round(self.speed_estimate_kmh, 1) if self.speed_estimate_kmh else None,
            "is_provisional": bool(self.is_provisional),
            "provisional_id": self.provisional_id,
            "resolved_plate": self.resolved_plate
        }


class Trajectory(Base):
    __tablename__ = 'trajectories'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    plate_text = Column(String(20), nullable=False, index=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    total_cameras = Column(Integer, nullable=False)
    distance_km = Column(Float, default=0.0)
    avg_speed_kmh = Column(Float, default=0.0)
    duration_minutes = Column(Float, default=0.0)
    # JSON encoded list of [lat, lon] or route points
    path_coordinates_json = Column(Text, nullable=False, default="[]")
    # JSON encoded list of camera IDs traversed in order
    camera_sequence_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    @property
    def path_coordinates(self):
        try:
            return json.loads(self.path_coordinates_json)
        except Exception:
            return []

    @path_coordinates.setter
    def path_coordinates(self, val):
        self.path_coordinates_json = json.dumps(val)

    @property
    def camera_sequence(self):
        try:
            return json.loads(self.camera_sequence_json)
        except Exception:
            return []

    @camera_sequence.setter
    def camera_sequence(self, val):
        self.camera_sequence_json = json.dumps(val)

    def to_dict(self):
        return {
            "id": self.id,
            "plate_text": self.plate_text,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "total_cameras": self.total_cameras,
            "distance_km": round(self.distance_km, 2),
            "avg_speed_kmh": round(self.avg_speed_kmh, 1),
            "duration_minutes": round(self.duration_minutes, 1),
            "path_coordinates": self.path_coordinates,
            "camera_sequence": self.camera_sequence
        }


class Alert(Base):
    __tablename__ = 'alerts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String(50), nullable=False, index=True)  # SPEED_ANOMALY, GEOFENCE_BREACH, FLAGGED_VEHICLE, CAMERA_OFFLINE
    severity = Column(String(20), nullable=False, default='WARNING')  # INFO, WARNING, CRITICAL
    message = Column(Text, nullable=False)
    plate_text = Column(String(20), nullable=True, index=True)
    camera_id = Column(Integer, ForeignKey('cameras.id'), nullable=True)
    metadata_json = Column(Text, default='{}')
    acknowledged = Column(Integer, default=0)  # SQLite boolean: 0=False, 1=True
    acknowledged_at = Column(DateTime, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "plate_text": self.plate_text,
            "camera_id": self.camera_id,
            "metadata": json.loads(self.metadata_json) if self.metadata_json else {},
            "acknowledged": bool(self.acknowledged),
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class GeofenceZone(Base):
    __tablename__ = 'geofence_zones'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    polygon_json = Column(Text, nullable=False, default='[]')  # JSON array of [lat, lng] pairs
    color = Column(String(20), default='#f43f5e')
    zone_type = Column(String(50), default='restricted')  # restricted, school_zone, curfew, monitoring
    active = Column(Integer, default=1)  # SQLite boolean
    curfew_start = Column(String(10), nullable=True)  # e.g. "22:00"
    curfew_end = Column(String(10), nullable=True)    # e.g. "05:00"
    speed_limit = Column(Float, nullable=True)        # e.g. 30.0 for school zones
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    @property
    def polygon(self):
        try:
            return json.loads(self.polygon_json)
        except Exception:
            return []

    @polygon.setter
    def polygon(self, val):
        self.polygon_json = json.dumps(val)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "polygon": self.polygon,
            "color": self.color,
            "zone_type": self.zone_type,
            "active": bool(self.active),
            "curfew_start": self.curfew_start,
            "curfew_end": self.curfew_end,
            "speed_limit": self.speed_limit,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FlaggedVehicle(Base):
    __tablename__ = 'flagged_vehicles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    plate_text = Column(String(20), nullable=False, unique=True, index=True)
    reason = Column(Text, default='')
    active = Column(Integer, default=1)  # SQLite boolean
    flagged_at = Column(DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "plate_text": self.plate_text,
            "reason": self.reason,
            "active": bool(self.active),
            "flagged_at": self.flagged_at.isoformat() if self.flagged_at else None,
        }


# Database connection helpers
DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "anpr.db"))
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"

def get_db_url():
    return os.getenv("DATABASE_URL", DEFAULT_DB_URL)

_GLOBAL_ENGINE = None
_GLOBAL_SESSIONMAKER = None

def get_engine(db_url=None):
    global _GLOBAL_ENGINE
    if db_url is None:
        db_url = get_db_url()
    
    if _GLOBAL_ENGINE is not None and str(_GLOBAL_ENGINE.url) == db_url:
        return _GLOBAL_ENGINE
    
    # Ensure directory exists for sqlite
    if db_url.startswith("sqlite:///"):
        from sqlalchemy import event
        sqlite_file = db_url.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(os.path.abspath(sqlite_file)), exist_ok=True)
        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False, "timeout": 15},
            pool_pre_ping=True
        )
        
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.close()
            except Exception:
                pass
                
        _GLOBAL_ENGINE = engine
        return engine
    
    engine = create_engine(db_url, pool_pre_ping=True)
    _GLOBAL_ENGINE = engine
    return engine

def init_db(engine=None):
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)

    # Lightweight auto-migration for SQLite/Postgres to add newly introduced columns
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        with engine.connect() as conn:
            if "cameras" in tables:
                cam_cols = [c["name"] for c in inspector.get_columns("cameras")]
                if "direction" not in cam_cols:
                    conn.execute(text("ALTER TABLE cameras ADD COLUMN direction VARCHAR(50) DEFAULT 'North → South'"))
                    conn.commit()

            if "plate_events" in tables:
                columns = [c["name"] for c in inspector.get_columns("plate_events")]
                if "direction" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN direction VARCHAR(50) DEFAULT 'Northbound'"))
                    conn.commit()
                if "speed_estimate_kmh" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN speed_estimate_kmh FLOAT"))
                    conn.commit()
                if "vehicle_color" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN vehicle_color VARCHAR(50) DEFAULT 'White'"))
                    conn.commit()
                if "make_model" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN make_model VARCHAR(100) DEFAULT 'Sedan'"))
                    conn.commit()
                if "is_provisional" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN is_provisional INTEGER DEFAULT 0"))
                    conn.commit()
                if "provisional_id" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN provisional_id VARCHAR(50)"))
                    conn.commit()
                if "resolved_plate" not in columns:
                    conn.execute(text("ALTER TABLE plate_events ADD COLUMN resolved_plate VARCHAR(20)"))
                    conn.commit()

            if "geofence_zones" in tables:
                geo_cols = [c["name"] for c in inspector.get_columns("geofence_zones")]
                if "curfew_start" not in geo_cols:
                    conn.execute(text("ALTER TABLE geofence_zones ADD COLUMN curfew_start VARCHAR(10)"))
                    conn.commit()
                if "curfew_end" not in geo_cols:
                    conn.execute(text("ALTER TABLE geofence_zones ADD COLUMN curfew_end VARCHAR(10)"))
                    conn.commit()
                if "speed_limit" not in geo_cols:
                    conn.execute(text("ALTER TABLE geofence_zones ADD COLUMN speed_limit FLOAT"))
                    conn.commit()
    except Exception as e:
        print(f"[DB Migration Warning] Column auto-migration check: {e}")

    return engine

def get_session(engine=None):
    global _GLOBAL_SESSIONMAKER
    if engine is None:
        engine = get_engine()
    if _GLOBAL_SESSIONMAKER is None or _GLOBAL_SESSIONMAKER.kw.get("bind") != engine:
        _GLOBAL_SESSIONMAKER = sessionmaker(bind=engine)
    return _GLOBAL_SESSIONMAKER()
