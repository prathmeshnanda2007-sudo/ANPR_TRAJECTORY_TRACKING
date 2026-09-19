"""
Geofence service for managing geographic zones and checking events against them.
Supports polygon-based geofence zones with point-in-polygon checks, curfew time windows,
school speed limits, and real-time intrusion alert dispatch.
"""

import json
import datetime
from typing import List, Optional, Dict, Any
from database.models import get_session, GeofenceZone, PlateEvent, Camera, Alert

# In-memory throttle cache: (zone_id, plate_text) -> last_alert_time
_RECENT_BREACHES: Dict[tuple, datetime.datetime] = {}
BREACH_THROTTLE_SECONDS = 300  # 5 minutes suppression per vehicle per zone


def _point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
    """
    Ray-casting algorithm to check if a point (lat, lng) is inside a polygon.
    polygon: list of [lat, lng] coordinate pairs defining the polygon vertices.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        lati, lngi = polygon[i]
        latj, lngj = polygon[j]

        # Ray cast horizontally across longitudes
        if ((lati > lat) != (latj > lat)) and (lng < (lngj - lngi) * (lat - lati) / (latj - lati) + lngi):
            inside = not inside
        j = i

    return inside


def _is_in_time_window(curfew_start: Optional[str], curfew_end: Optional[str], check_dt: datetime.datetime) -> bool:
    """
    Evaluates whether check_dt falls between curfew_start and curfew_end (format 'HH:MM').
    Handles overnight intervals like 22:00 -> 05:00 seamlessly.
    """
    if not curfew_start or not curfew_end:
        return True

    try:
        sh, sm = map(int, curfew_start.split(":"))
        eh, em = map(int, curfew_end.split(":"))
        t_start = datetime.time(sh, sm)
        t_end = datetime.time(eh, em)
        t_cur = check_dt.time()

        if t_start <= t_end:
            # Daytime interval (e.g. 07:00 to 16:00)
            return t_start <= t_cur <= t_end
        else:
            # Overnight interval (e.g. 22:00 to 05:00)
            return t_cur >= t_start or t_cur <= t_end
    except Exception:
        return True


class GeofenceService:
    """Manages geofence zones and event intersection checks."""

    def __init__(self, session=None):
        self.session = session or get_session()

    def create_zone(
        self,
        name: str,
        polygon_coords: list,
        color: str = "#f43f5e",
        zone_type: str = "restricted",
        curfew_start: Optional[str] = None,
        curfew_end: Optional[str] = None,
        speed_limit: Optional[float] = None
    ) -> dict:
        """
        Create a new geofence zone.
        polygon_coords: list of [lat, lng] pairs defining the polygon.
        """
        zone = GeofenceZone(
            name=name,
            polygon_json=json.dumps(polygon_coords),
            color=color,
            zone_type=zone_type,
            active=1,
            curfew_start=curfew_start,
            curfew_end=curfew_end,
            speed_limit=speed_limit,
            created_at=datetime.datetime.utcnow(),
        )
        self.session.add(zone)
        self.session.commit()
        return zone.to_dict()

    def get_all_zones(self, active_only: bool = True) -> List[dict]:
        """Get all geofence zones."""
        query = self.session.query(GeofenceZone)
        if active_only:
            query = query.filter(GeofenceZone.active == 1)
        return [z.to_dict() for z in query.order_by(GeofenceZone.created_at.desc()).all()]

    def get_zone(self, zone_id: int) -> Optional[dict]:
        """Get a specific geofence zone."""
        zone = self.session.query(GeofenceZone).filter(GeofenceZone.id == zone_id).first()
        return zone.to_dict() if zone else None

    def delete_zone(self, zone_id: int) -> bool:
        """Deactivate a geofence zone."""
        zone = self.session.query(GeofenceZone).filter(GeofenceZone.id == zone_id).first()
        if zone:
            zone.active = 0
            self.session.commit()
            return True
        return False

    def check_point_in_geofences(
        self,
        lat: float,
        lng: float,
        plate_text: Optional[str] = None,
        camera_id: Optional[int] = None,
        speed: Optional[float] = None,
        timestamp: Optional[datetime.datetime] = None
    ) -> List[dict]:
        """
        Evaluates point (lat, lng) against all active geofenced zones.
        Triggers and persists GEOFENCE_BREACH alert if restricted, curfew breached, or speed limit exceeded.
        """
        now = timestamp or datetime.datetime.utcnow()
        zones = self.session.query(GeofenceZone).filter(GeofenceZone.active == 1).all()
        alerts = []

        camera = self.session.query(Camera).filter(Camera.id == camera_id).first() if camera_id else None
        cam_name = camera.name if camera else f"Camera #{camera_id}" if camera_id else "GIS Coordinate"

        for zone in zones:
            poly = zone.polygon
            if not poly or len(poly) < 3:
                continue

            if not _point_in_polygon(lat, lng, poly):
                continue

            # Point is inside zone polygon! Check constraints
            is_breach = False
            severity = "WARNING"
            breach_reason = ""

            # Check curfew window
            if zone.zone_type == "curfew" or (zone.curfew_start and zone.curfew_end):
                if _is_in_time_window(zone.curfew_start, zone.curfew_end, now):
                    is_breach = True
                    severity = "CRITICAL"
                    breach_reason = f"Curfew Violation ({zone.curfew_start} - {zone.curfew_end})"
                else:
                    # Outside curfew hours, ignore
                    continue
            elif zone.zone_type == "school_zone" or zone.speed_limit:
                # Speed limit evaluation
                limit = zone.speed_limit or 30.0
                if speed is not None and speed > limit:
                    is_breach = True
                    severity = "WARNING"
                    breach_reason = f"School Zone Speeding ({speed:.1f} km/h > {limit:.1f} km/h limit)"
                elif zone.curfew_start and zone.curfew_end:
                    # If within school hours
                    if _is_in_time_window(zone.curfew_start, zone.curfew_end, now):
                        is_breach = True
                        severity = "INFO"
                        breach_reason = f"Monitored School Zone Transit ({zone.name})"
                else:
                    continue
            else:
                # Default restricted / VIP corridor: any entry is a breach
                is_breach = True
                severity = "CRITICAL"
                breach_reason = f"Unauthorized Intrusion in Restricted Zone"

            if not is_breach:
                continue

            # Throttle duplicate alerts for same vehicle in same zone within 5 minutes
            plate_key = plate_text or "UNKNOWN"
            cache_key = (zone.id, plate_key)
            last_alert = _RECENT_BREACHES.get(cache_key)
            if last_alert and (now - last_alert).total_seconds() < BREACH_THROTTLE_SECONDS:
                continue

            _RECENT_BREACHES[cache_key] = now

            # Construct Alert
            msg = (
                f"🛡️ GEOFENCE BREACH: Vehicle {plate_key} entered '{zone.name}' "
                f"at {cam_name} ({breach_reason})"
            )
            meta = {
                "zone_id": zone.id,
                "zone_name": zone.name,
                "zone_type": zone.zone_type,
                "latitude": lat,
                "longitude": lng,
                "speed_kmh": speed,
                "reason": breach_reason
            }

            alert = Alert(
                alert_type="GEOFENCE_BREACH",
                severity=severity,
                message=msg,
                plate_text=plate_key,
                camera_id=camera_id,
                metadata_json=json.dumps(meta),
                timestamp=now
            )
            self.session.add(alert)
            self.session.commit()
            self.session.refresh(alert)
            alerts.append(alert.to_dict())

        return alerts

    def get_events_in_zone(self, zone_id: int, limit: int = 100) -> List[dict]:
        """Find plate events from cameras that fall within a geofence zone."""
        zone = self.session.query(GeofenceZone).filter(GeofenceZone.id == zone_id).first()
        if not zone:
            return []

        polygon = zone.polygon
        if not polygon or len(polygon) < 3:
            return []

        # Find cameras inside the geofence polygon
        cameras = self.session.query(Camera).all()
        camera_ids_in_zone = [
            cam.id for cam in cameras
            if _point_in_polygon(cam.latitude, cam.longitude, polygon)
        ]

        if not camera_ids_in_zone:
            return []

        events = (
            self.session.query(PlateEvent)
            .filter(PlateEvent.camera_id.in_(camera_ids_in_zone))
            .order_by(PlateEvent.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [e.to_dict() for e in events]

    def check_camera_in_zones(self, camera_id: int) -> List[dict]:
        """Check which geofence zones a camera falls within."""
        cam = self.session.query(Camera).filter(Camera.id == camera_id).first()
        if not cam:
            return []

        zones = self.session.query(GeofenceZone).filter(GeofenceZone.active == 1).all()
        matching = []
        for zone in zones:
            polygon = zone.polygon
            if polygon and _point_in_polygon(cam.latitude, cam.longitude, polygon):
                matching.append(zone.to_dict())
        return matching
