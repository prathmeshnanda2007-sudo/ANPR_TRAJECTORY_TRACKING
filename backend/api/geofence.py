"""
Geofence API routes for managing geographic zones.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from database.models import get_session
from backend.services.geofence_service import GeofenceService, _point_in_polygon

router = APIRouter(prefix="/geofences", tags=["Geofences"])


class CreateGeofenceRequest(BaseModel):
    name: str
    polygon: List[List[float]]  # list of [lat, lng] pairs
    color: str = "#f43f5e"
    zone_type: str = "restricted"  # restricted, school_zone, curfew, monitoring
    curfew_start: Optional[str] = None
    curfew_end: Optional[str] = None
    speed_limit: Optional[float] = None


class CheckPointRequest(BaseModel):
    latitude: float
    longitude: float
    plate_text: Optional[str] = None
    speed_kmh: Optional[float] = None


@router.post("/check-point")
def check_point(req: CheckPointRequest):
    """Check which active geofence zones a coordinate point falls inside."""
    session = get_session()
    try:
        service = GeofenceService(session)
        zones = service.get_all_zones(active_only=True)
        matching = []
        for zone in zones:
            poly = zone.get("polygon", [])
            if poly and _point_in_polygon(req.latitude, req.longitude, poly):
                matching.append(zone)
        return {
            "latitude": req.latitude,
            "longitude": req.longitude,
            "plate_text": req.plate_text,
            "inside_zones": matching,
            "in_restricted_zone": len(matching) > 0,
        }
    finally:
        session.close()


@router.get("")
def list_geofences(active_only: bool = Query(True)):
    """Get all geofence zones."""
    session = get_session()
    try:
        service = GeofenceService(session)
        return service.get_all_zones(active_only=active_only)
    finally:
        session.close()


@router.post("")
def create_geofence(req: CreateGeofenceRequest):
    """Create a new geofence zone."""
    if len(req.polygon) < 3:
        raise HTTPException(status_code=400, detail="A geofence polygon must have at least 3 vertices.")
    session = get_session()
    try:
        service = GeofenceService(session)
        return service.create_zone(
            name=req.name,
            polygon_coords=req.polygon,
            color=req.color,
            zone_type=req.zone_type,
            curfew_start=req.curfew_start,
            curfew_end=req.curfew_end,
            speed_limit=req.speed_limit,
        )
    finally:
        session.close()


@router.get("/{zone_id}")
def get_geofence(zone_id: int):
    """Get a specific geofence zone."""
    session = get_session()
    try:
        service = GeofenceService(session)
        zone = service.get_zone(zone_id)
        if not zone:
            raise HTTPException(status_code=404, detail=f"Geofence zone {zone_id} not found")
        return zone
    finally:
        session.close()


@router.delete("/{zone_id}")
def delete_geofence(zone_id: int):
    """Delete (deactivate) a geofence zone."""
    session = get_session()
    try:
        service = GeofenceService(session)
        success = service.delete_zone(zone_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Geofence zone {zone_id} not found")
        return {"success": True, "zone_id": zone_id}
    finally:
        session.close()


@router.get("/{zone_id}/events")
def get_geofence_events(zone_id: int, limit: int = Query(100, ge=1, le=500)):
    """Get plate events from cameras within a geofence zone."""
    session = get_session()
    try:
        service = GeofenceService(session)
        zone = service.get_zone(zone_id)
        if not zone:
            raise HTTPException(status_code=404, detail=f"Geofence zone {zone_id} not found")
        events = service.get_events_in_zone(zone_id, limit=limit)
        return {"zone": zone, "events_count": len(events), "events": events}
    finally:
        session.close()
