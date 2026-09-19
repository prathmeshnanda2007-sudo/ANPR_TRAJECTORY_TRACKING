from typing import Optional
from fastapi import APIRouter, Query
from analytics.traffic_volume import TrafficVolumeAnalyzer
from analytics.od_matrix import ODMatrixAnalyzer
from analytics.heatmap import HeatmapEngine
from analytics.route_prediction import RoutePredictionEngine
from backend.services.camera_service import CameraHealthService
from database.models import get_session

router = APIRouter(prefix="/analytics", tags=["Traffic Analytics"])

@router.get("/summary")
@router.get("/metrics")
def get_summary():
    session = get_session()
    try:
        analyzer = TrafficVolumeAnalyzer(session)
        return analyzer.get_summary_metrics()
    finally:
        session.close()

@router.get("/hourly-volume")
def get_hourly_volume():
    session = get_session()
    try:
        analyzer = TrafficVolumeAnalyzer(session)
        return analyzer.get_hourly_traffic_volume()
    finally:
        session.close()

@router.get("/vehicle-types")
def get_vehicle_types():
    session = get_session()
    try:
        analyzer = TrafficVolumeAnalyzer(session)
        return analyzer.get_vehicle_type_distribution()
    finally:
        session.close()

@router.get("/busiest-cameras")
def get_busiest_cameras():
    session = get_session()
    try:
        analyzer = TrafficVolumeAnalyzer(session)
        return analyzer.get_busiest_cameras(limit=8)
    finally:
        session.close()

@router.get("/od-matrix")
def get_od_matrix():
    session = get_session()
    try:
        analyzer = ODMatrixAnalyzer(session)
        return analyzer.get_od_matrix(limit=10)
    finally:
        session.close()

@router.get("/speed-anomalies")
def get_speed_anomalies():
    session = get_session()
    try:
        analyzer = ODMatrixAnalyzer(session)
        return analyzer.get_speed_anomalies(threshold_kmh=75.0, limit=10)
    finally:
        session.close()

@router.get("/speed-violations-heatmap")
def get_speed_violations_heatmap(threshold_kmh: float = Query(75.0, description="Speed violation threshold in km/h")):
    """Get live-updating road segment speed violation choropleth and hotspot matrix."""
    from analytics.speed_heatmap import SpeedViolationHeatmapEngine
    session = get_session()
    try:
        engine = SpeedViolationHeatmapEngine(session)
        return engine.get_speed_violation_segments(threshold_kmh=threshold_kmh)
    finally:
        session.close()

# ── New Analytics Endpoints ──

@router.get("/heatmap")
def get_heatmap():
    """Get hour-by-camera detection density heatmap matrix."""
    session = get_session()
    try:
        engine = HeatmapEngine(session)
        return engine.get_hour_camera_heatmap()
    finally:
        session.close()

@router.get("/peak-hours")
def get_peak_hours():
    """Get peak traffic hour analysis with time-band classification."""
    session = get_session()
    try:
        engine = HeatmapEngine(session)
        return engine.get_peak_hours()
    finally:
        session.close()

@router.get("/camera-health")
def get_camera_health():
    """Get detailed camera health monitoring matrix."""
    session = get_session()
    try:
        service = CameraHealthService(session)
        return service.get_camera_health_matrix()
    finally:
        session.close()

@router.get("/system-overview")
def get_system_overview():
    """Get high-level system health overview."""
    session = get_session()
    try:
        service = CameraHealthService(session)
        return service.get_system_overview()
    finally:
        session.close()

@router.get("/predict-route")
def predict_route(
    plate: Optional[str] = Query(None, description="Vehicle plate to predict route for"),
    camera_id: Optional[int] = Query(None, description="Camera ID to predict next destinations from"),
    steps: int = Query(3, ge=1, le=10),
):
    """Predict likely next cameras using Markov-chain transition probabilities."""
    session = get_session()
    try:
        engine = RoutePredictionEngine(session)
        if plate:
            return engine.predict_route_for_plate(plate, steps=steps)
        elif camera_id is not None:
            predictions = engine.predict_next_cameras(camera_id, top_n=steps)
            return {"camera_id": camera_id, "predictions": predictions}
        else:
            return engine.get_transition_matrix_summary()
    finally:
        session.close()

@router.get("/congestion")
def get_congestion():
    """Get real-time congestion scores and status for all smart city cameras."""
    session = get_session()
    try:
        analyzer = TrafficVolumeAnalyzer(session)
        return analyzer.get_congestion_status()
    finally:
        session.close()

