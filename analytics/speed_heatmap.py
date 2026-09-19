"""
Speed-Zone Violation Heatmap Engine.
Analyzes road segment transit speeds and camera-to-camera velocity differentials
to produce a choropleth overlay color-coding city corridors by speeding risk.
"""

from collections import defaultdict
from typing import Dict, List, Any
import math
from database.models import get_session, Camera, Trajectory, PlateEvent
from analytics.trajectory import haversine_distance_km

# Connected smart city road corridor segments (Bhubaneswar arterial network)
CORRIDOR_SEGMENTS = [
    (1, 2), (2, 3), (3, 4),   # Rasulgarh -> Vani Vihar -> Acharya Vihar -> Jaydev Vihar
    (12, 4), (4, 3), (3, 2), (2, 1), # Baramunda -> Jaydev -> Acharya -> Vani -> Rasulgarh
    (6, 7), (7, 5), (5, 12),  # Master Canteen -> Kalpana -> Khandagiri -> Baramunda
    (8, 9), (9, 10), (10, 11),# Chandrasekharpur -> Patia -> KIIT -> Infocity
    (5, 12), (12, 4), (4, 8), (8, 9), (9, 10), # Khandagiri -> Baramunda -> Jaydev -> Patia
    (11, 10), (10, 9), (9, 8), (8, 4), (4, 3), (3, 1), # Infocity -> Rasulgarh
    (7, 6), (6, 3), (3, 4),   # Kalpana -> Master Canteen -> Acharya -> Jaydev
    (4, 8), (8, 9), (9, 10)   # Jaydev -> Patia -> KIIT
]

class SpeedViolationHeatmapEngine:
    def __init__(self, session=None):
        self.session = session or get_session()

    def get_speed_violation_segments(self, threshold_kmh: float = 75.0) -> Dict[str, Any]:
        """
        Calculates speed statistics across road segments connecting camera nodes.
        Returns GeoJSON-ready segment polylines and camera violation hotspots.
        """
        cameras = {c.id: c for c in self.session.query(Camera).all()}
        trajectories = self.session.query(Trajectory).all()

        # Track segment data: (min_id, max_id) -> aggregated speeds and violations
        segment_stats = defaultdict(lambda: {
            "speeds": [],
            "violations": 0,
            "plates": set(),
            "trips": 0
        })

        # 1. Analyze multi-hop trajectories for segment velocities
        for traj in trajectories:
            coords = traj.path_coordinates or []
            if len(coords) < 2:
                continue

            for i in range(len(coords) - 1):
                c1 = coords[i]
                c2 = coords[i + 1]
                cam1_id = c1.get("camera_id")
                cam2_id = c2.get("camera_id")
                if not cam1_id or not cam2_id or cam1_id == cam2_id:
                    continue

                seg_key = (cam1_id, cam2_id)
                # Compute segment speed from points if recorded, else trajectory avg
                seg_speed = c2.get("speed_kmh") or c1.get("speed_kmh") or traj.avg_speed_kmh or 45.0
                segment_stats[seg_key]["speeds"].append(seg_speed)
                segment_stats[seg_key]["trips"] += 1
                segment_stats[seg_key]["plates"].add(traj.plate_text)
                if seg_speed >= threshold_kmh:
                    segment_stats[seg_key]["violations"] += 1

        # 2. Also incorporate recent PlateEvent speeds on camera pairs
        recent_events = (
            self.session.query(PlateEvent)
            .filter(PlateEvent.speed_estimate_kmh != None)
            .order_by(PlateEvent.timestamp.desc())
            .limit(300)
            .all()
        )
        cam_recent_speeds = defaultdict(list)
        for ev in recent_events:
            if ev.speed_estimate_kmh and ev.speed_estimate_kmh > 0:
                cam_recent_speeds[ev.camera_id].append(ev.speed_estimate_kmh)

        # 3. Ensure all predefined corridor links are represented
        all_segments = set(CORRIDOR_SEGMENTS) | set(segment_stats.keys())
        # Deduplicate bidirectional representation for cleaner map rendering
        unique_segments = {}

        for orig_id, dest_id in all_segments:
            cam1 = cameras.get(orig_id)
            cam2 = cameras.get(dest_id)
            if not cam1 or not cam2:
                continue

            pair_key = tuple(sorted([orig_id, dest_id]))
            stats_fwd = segment_stats.get((orig_id, dest_id), {"speeds": [], "violations": 0, "plates": set(), "trips": 0})
            stats_rev = segment_stats.get((dest_id, orig_id), {"speeds": [], "violations": 0, "plates": set(), "trips": 0})

            combined_speeds = stats_fwd["speeds"] + stats_rev["speeds"]
            if not combined_speeds:
                # Fallback to junction speed estimates
                cam_speeds = cam_recent_speeds.get(orig_id, []) + cam_recent_speeds.get(dest_id, [])
                combined_speeds = cam_speeds if cam_speeds else [42.0]

            total_violations = stats_fwd["violations"] + stats_rev["violations"]
            # Count violations from combined speeds if not already counted
            if total_violations == 0:
                total_violations = sum(1 for s in combined_speeds if s >= threshold_kmh)

            avg_speed = round(sum(combined_speeds) / len(combined_speeds), 1) if combined_speeds else 40.0
            max_speed = round(max(combined_speeds), 1) if combined_speeds else avg_speed

            # Segment length in km
            dist_km = round(haversine_distance_km(cam1.latitude, cam1.longitude, cam2.latitude, cam2.longitude), 2)

            # Determine risk status and coloring
            if total_violations >= 3 or max_speed >= 95.0 or avg_speed >= 75.0:
                status = "CRITICAL"
                color = "#f43f5e"      # Neon Crimson / Red
                glow_color = "rgba(244, 63, 94, 0.4)"
                level_label = "Severe Speeding Zone (>75 km/h)"
                speed_limit = 60.0
            elif total_violations >= 1 or max_speed >= 70.0 or avg_speed >= 58.0:
                status = "WARNING"
                color = "#f59e0b"      # Neon Amber / Orange
                glow_color = "rgba(245, 158, 11, 0.35)"
                level_label = "Moderate Over-Speed (60-75 km/h)"
                speed_limit = 60.0
            else:
                status = "NORMAL"
                color = "#10b981"      # Emerald Green
                glow_color = "rgba(16, 185, 129, 0.25)"
                level_label = "Compliant Flow (<60 km/h)"
                speed_limit = 60.0

            unique_segments[pair_key] = {
                "segment_id": f"SEG-{cam1.id}-{cam2.id}",
                "origin_camera_id": cam1.id,
                "origin_name": cam1.name,
                "origin_location": cam1.location_name,
                "destination_camera_id": cam2.id,
                "destination_name": cam2.name,
                "destination_location": cam2.location_name,
                "coordinates": [
                    [cam1.latitude, cam1.longitude],
                    [cam2.latitude, cam2.longitude]
                ],
                "distance_km": dist_km,
                "avg_speed_kmh": avg_speed,
                "max_speed_kmh": max_speed,
                "speed_limit_kmh": speed_limit,
                "violations_count": total_violations,
                "trips_count": stats_fwd["trips"] + stats_rev["trips"],
                "status": status,
                "color": color,
                "glow_color": glow_color,
                "level_label": level_label
            }

        segments_list = list(unique_segments.values())

        # 4. Camera intersection hotspots
        camera_hotspots = []
        for cam_id, cam in cameras.items():
            # Count violations connected to this camera
            c_violations = sum(
                s["violations_count"] for s in segments_list
                if s["origin_camera_id"] == cam_id or s["destination_camera_id"] == cam_id
            )
            c_speeds = cam_recent_speeds.get(cam_id, [])
            c_avg_speed = round(sum(c_speeds) / len(c_speeds), 1) if c_speeds else 42.0
            c_max_speed = round(max(c_speeds), 1) if c_speeds else c_avg_speed

            if c_violations >= 3 or c_max_speed >= 80.0:
                c_status = "CRITICAL"
                c_color = "#f43f5e"
            elif c_violations >= 1 or c_max_speed >= 65.0:
                c_status = "WARNING"
                c_color = "#f59e0b"
            else:
                c_status = "NORMAL"
                c_color = "#10b981"

            camera_hotspots.append({
                "camera_id": cam.id,
                "name": cam.name,
                "location": cam.location_name,
                "latitude": cam.latitude,
                "longitude": cam.longitude,
                "violations_count": c_violations,
                "avg_speed_kmh": c_avg_speed,
                "max_speed_kmh": c_max_speed,
                "status": c_status,
                "color": c_color,
                "radius_meters": 280 + min(c_violations * 120, 700)
            })

        total_violations = sum(s["violations_count"] for s in segments_list)
        critical_segments = sum(1 for s in segments_list if s["status"] == "CRITICAL")
        warning_segments = sum(1 for s in segments_list if s["status"] == "WARNING")
        max_overall_speed = max([s["max_speed_kmh"] for s in segments_list] or [0.0])

        return {
            "summary": {
                "total_segments": len(segments_list),
                "total_violations": total_violations,
                "critical_segments": critical_segments,
                "warning_segments": warning_segments,
                "compliant_segments": len(segments_list) - critical_segments - warning_segments,
                "max_clocked_speed_kmh": max_overall_speed,
                "speed_threshold_kmh": threshold_kmh
            },
            "segments": segments_list,
            "hotspots": camera_hotspots
        }
