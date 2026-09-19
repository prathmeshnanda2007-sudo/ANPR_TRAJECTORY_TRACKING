"""
Autonomous Real-Time City Traffic Streamer Engine.
Continuously simulates real vehicle movements and ANPR detections across the smart city
camera network, persisting sightings to the database and broadcasting over WebSockets.
Includes:
- Speed-zone anomaly checks & alerts
- Geofenced zone & curfew evaluations
- Provisional Fallback Re-ID for occluded plates with downstream confirmation upgrades
"""

import asyncio
import datetime
import random
from typing import List, Dict, Optional, Tuple
from database.models import get_session, Camera, PlateEvent, Trajectory
from analytics.trajectory import TrajectoryEngine
from backend.services.alert_service import AlertService
from backend.services.geofence_service import GeofenceService
from backend.services.websocket_manager import ws_manager

# Bhubaneswar Smart City Corridors across Connected Arterials
CORRIDOR_CAMERAS = [
    # Corridor 0: Rasulgarh -> Vani Vihar -> Acharya Vihar -> Jaydev Vihar
    [1, 2, 3, 4],
    # Corridor 1: Baramunda -> Jaydev Vihar -> Acharya Vihar -> Vani Vihar -> Rasulgarh
    [12, 4, 3, 2, 1],
    # Corridor 2: Master Canteen -> Kalpana -> Khandagiri -> Baramunda
    [6, 7, 5, 12],
    # Corridor 3: Chandrasekharpur -> Patia -> KIIT -> Infocity
    [8, 9, 10, 11],
    # Corridor 4: Khandagiri -> Baramunda -> Jaydev Vihar -> Patia
    [5, 12, 4, 8, 9, 10],
    # Corridor 5: Reverse Infocity -> Patia -> Jaydev Vihar -> Rasulgarh
    [11, 10, 9, 8, 4, 3, 1],
    # Corridor 6: Kalpana -> Master Canteen -> Acharya Vihar -> Jaydev Vihar
    [7, 6, 3, 4],
    # Corridor 7: Jaydev Vihar -> Chandrasekharpur -> Patia -> KIIT
    [4, 8, 9, 10]
]

# Dedicated ambient fleet for live simulation
STREAM_COMMUTER_PLATES = [
    {"plate": "OD02TR1011", "type": "Car", "color": "Silver", "make": "Sedan"},
    {"plate": "OD02CV4455", "type": "Car", "color": "White", "make": "SUV"},
    {"plate": "OD33AB8899", "type": "Bus", "color": "Blue", "make": "City Bus"},
    {"plate": "OD14TK2200", "type": "Truck", "color": "Yellow", "make": "Heavy Truck"},
    {"plate": "OD05MC3311", "type": "Motorcycle", "color": "Black", "make": "Bike"},
    {"plate": "OD02PX7788", "type": "Car", "color": "Red", "make": "Sedan"},
    {"plate": "OD02KL5566", "type": "Car", "color": "Grey", "make": "Sedan"},
    {"plate": "OD07BB9001", "type": "Car", "color": "White", "make": "Hatchback"},
    {"plate": "OD10ZZ4040", "type": "Car", "color": "Blue", "make": "Sedan"},
    {"plate": "OD02MN3131", "type": "Car", "color": "Black", "make": "SUV"},
    {"plate": "OD33CC1212", "type": "Bus", "color": "Green", "make": "Bus"},
    {"plate": "OD05BK8800", "type": "Motorcycle", "color": "Red", "make": "Bike"},
    {"plate": "OD14HT9911", "type": "Truck", "color": "Brown", "make": "Truck"},
]

# Camera junction speed profiles for realistic traffic flow
JUNCTION_SPEED_PROFILES = {
    1: (18.0, 26.0),  # Rasulgarh: Heavy chokepoint crawl
    2: (34.0, 43.0),  # Vani Vihar
    3: (39.0, 48.0),  # Acharya Vihar
    4: (22.0, 31.0),  # Jaydev Vihar: Heavy congestion
    5: (58.0, 68.0),  # Khandagiri: Express bypass
    6: (28.0, 36.0),  # Master Canteen: Downtown core
    7: (43.0, 52.0),  # Kalpana: Heritage link
    8: (48.0, 58.0),  # Chandrasekharpur: Boulevard
    9: (38.0, 46.0),  # Patia Square: Tech corridor
    10: (32.0, 40.0), # KIIT Square: Campus zone
    11: (62.0, 72.0), # Infocity: High-speed expressway
    12: (30.0, 38.0), # Baramunda: Bus terminal
}


class ActiveVehicleJourney:
    def __init__(self, plate_info: dict, corridor: List[int], start_provisional: bool = False):
        self.plate = plate_info["plate"]
        self.v_type = plate_info["type"]
        self.color = plate_info.get("color", "White")
        self.make = plate_info.get("make", "Sedan")
        self.corridor = corridor
        self.current_step = 0
        self.completed = False
        self.is_provisional = start_provisional
        self.provisional_id = f"UNVERIFIED-#{random.randint(10, 99)}" if start_provisional else None

    def next_camera_id(self) -> Optional[int]:
        if self.current_step < len(self.corridor):
            cam_id = self.corridor[self.current_step]
            self.current_step += 1
            if self.current_step >= len(self.corridor):
                self.completed = True
            return cam_id
        self.completed = True
        return None


class CityTrafficStreamEngine:
    def __init__(self, interval_seconds: float = 3.5):
        self.interval = interval_seconds
        self.running = False
        self._task = None
        self._active_journeys: List[ActiveVehicleJourney] = []
        self._seed_active_journeys()

    def _seed_active_journeys(self):
        for idx, plate_info in enumerate(STREAM_COMMUTER_PLATES[:8]):
            corridor = random.choice(CORRIDOR_CAMERAS)
            # Make 1 vehicle start as occluded/provisional for immediate live demonstration
            start_prov = (idx == 2)
            journey = ActiveVehicleJourney(plate_info, corridor, start_provisional=start_prov)
            journey.current_step = random.randint(0, len(corridor) - 1) if not start_prov else 0
            self._active_journeys.append(journey)

    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._stream_loop())
        print(f"[TrafficStreamer] Live City ANPR Traffic Streamer started (Interval: {self.interval}s)")

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("[TrafficStreamer] Live City ANPR Traffic Streamer stopped.")

    async def _stream_loop(self):
        while self.running:
            try:
                await asyncio.sleep(self.interval)
                await self.generate_single_sighting()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[TrafficStreamer Error] Sighting loop error: {e}")
                await asyncio.sleep(2.0)

    async def generate_single_sighting(self):
        """Picks an active journey, steps it to the next camera, records the event and broadcasts it."""
        # Clean completed journeys and replenish with fresh distinct commuters
        self._active_journeys = [j for j in self._active_journeys if not j.completed]
        if len(self._active_journeys) < 6:
            plate_info = random.choice(STREAM_COMMUTER_PLATES)
            corridor = random.choice(CORRIDOR_CAMERAS)
            # 1 in 5 new journeys will start with an occluded plate / fallback ID
            start_prov = (random.random() < 0.25)
            self._active_journeys.append(ActiveVehicleJourney(plate_info, corridor, start_provisional=start_prov))

        if not self._active_journeys:
            return

        journey = random.choice(self._active_journeys)
        step_number = journey.current_step
        cam_id = journey.next_camera_id()
        if not cam_id:
            return

        # Check provisional status:
        # If journey was provisional and this is step >= 1, vehicle is cleanly identified at downstream camera!
        resolving_provisional_id = None
        current_plate_to_report = journey.plate
        current_is_provisional = False
        current_provisional_id = None

        if journey.is_provisional:
            if step_number == 0:
                # First sighting: Occluded / muddy plate, fallback coarse fingerprint
                current_plate_to_report = journey.provisional_id
                current_is_provisional = True
                current_provisional_id = journey.provisional_id
            else:
                # Downstream camera gets clean read! Re-ID resolved
                resolving_provisional_id = journey.provisional_id
                journey.is_provisional = False
                journey.provisional_id = None
                current_plate_to_report = journey.plate

        # Offload synchronous SQLite operations to threadpool
        try:
            result = await asyncio.to_thread(
                self._sync_record_sighting,
                cam_id,
                current_plate_to_report,
                journey.v_type,
                journey.color,
                journey.make,
                current_is_provisional,
                current_provisional_id,
                resolving_provisional_id
            )
            if not result:
                return

            event_dict, alerts_to_broadcast, reid_upgrade = result

            # Broadcast alerts
            for alert in alerts_to_broadcast:
                try:
                    await ws_manager.broadcast_alert(alert)
                except Exception:
                    pass

            # Broadcast Re-ID Upgrade event if resolved
            if reid_upgrade:
                try:
                    await ws_manager.broadcast_reid_upgrade(reid_upgrade)
                except Exception:
                    pass

            # Broadcast sighting
            if event_dict:
                try:
                    await ws_manager.broadcast_event(event_dict)
                except Exception:
                    pass
        except Exception as e:
            print(f"[TrafficStreamer Warning] Error in sighting processing: {e}")

    def _sync_record_sighting(
        self,
        cam_id: int,
        plate: str,
        v_type: str,
        color: str,
        make: str,
        is_provisional: bool = False,
        provisional_id: Optional[str] = None,
        resolving_provisional_id: Optional[str] = None
    ) -> Optional[Tuple[dict, List[dict], Optional[dict]]]:
        """Runs synchronously in a separate threadpool worker."""
        session = get_session()
        alerts_to_broadcast = []
        reid_upgrade = None

        try:
            cam = session.query(Camera).filter(Camera.id == cam_id).first()
            if not cam or cam.status != "ACTIVE":
                return None

            min_sp, max_sp = JUNCTION_SPEED_PROFILES.get(cam.id, (35.0, 50.0))
            type_mod = -3.0 if v_type in ["Truck", "Bus"] else 0.0
            # Occasional speeder on expressways (CAM 5, 11)
            is_speeder = (cam_id in [5, 11] and random.random() < 0.20)
            if is_speeder:
                speed = random.uniform(82.0, 94.0)
            else:
                speed = max(16.0, random.uniform(min_sp, max_sp) + type_mod)

            conf = round(random.uniform(0.38, 0.48), 2) if is_provisional else round(random.uniform(0.92, 0.99), 3)
            now = datetime.datetime.utcnow()

            direction = "Northbound"
            if cam_id in [10, 9, 8]:
                direction = "Westbound"
            elif cam_id in [11, 12]:
                direction = "Southbound"
            elif cam_id in [1, 2, 3]:
                direction = "Eastbound"

            # 1. Handle Re-ID Upgrade resolution if vehicle was previously provisional
            if resolving_provisional_id:
                # Update earlier provisional events in DB to link to the confirmed plate
                prior_events = (
                    session.query(PlateEvent)
                    .filter(PlateEvent.provisional_id == resolving_provisional_id)
                    .all()
                )
                for pe in prior_events:
                    pe.resolved_plate = plate
                session.commit()

                reid_msg = (
                    f"🔄 RE-ID RESOLVED: Fallback {resolving_provisional_id} verified as "
                    f"{plate} at {cam.name} ({color} {v_type})"
                )
                reid_upgrade = {
                    "provisional_id": resolving_provisional_id,
                    "resolved_plate": plate,
                    "camera_id": cam.id,
                    "camera_name": cam.name,
                    "camera_location": cam.location_name,
                    "vehicle_type": v_type,
                    "vehicle_color": color,
                    "confidence": conf,
                    "message": reid_msg
                }

                # Record INFO alert for Re-ID resolution
                alert_svc = AlertService(session)
                try:
                    res_alert = alert_svc.create_alert(
                        alert_type="REID_RESOLUTION",
                        severity="INFO",
                        message=reid_msg,
                        plate_text=plate,
                        camera_id=cam.id
                    )
                    alerts_to_broadcast.append(res_alert)
                except Exception:
                    pass

            # 2. Persist current sighting event
            event = PlateEvent(
                camera_id=cam.id,
                plate_text=plate,
                confidence=conf,
                timestamp=now,
                vehicle_type=v_type,
                vehicle_color=color,
                make_model=make,
                direction=cam.direction or direction,
                speed_estimate_kmh=round(speed, 1),
                is_provisional=1 if is_provisional else 0,
                provisional_id=provisional_id,
                resolved_plate=None
            )
            session.add(event)
            session.commit()
            session.refresh(event)

            event_dict = event.to_dict()

            # 3. Update trajectory
            try:
                traj_engine = TrajectoryEngine(session)
                traj_engine.build_trajectories_for_plate(plate, commit=True)
                if resolving_provisional_id:
                    # Also rebuild for resolved plate to incorporate previous steps
                    traj_engine.build_trajectories_for_plate(plate, commit=True)
            except Exception:
                pass

            # 4. Alert checks
            alert_svc = AlertService(session)

            # Blacklist Match Alert
            try:
                bl_alert = alert_svc.check_blacklist_match(plate, cam.id, cam.name)
                if bl_alert:
                    alerts_to_broadcast.append(bl_alert)
            except Exception:
                pass

            # Suspicious Speed / Route Anomaly Alert
            try:
                sp_alert = alert_svc.check_suspicious_route(plate, cam.id, speed)
                if sp_alert:
                    alerts_to_broadcast.append(sp_alert)
                elif speed > 75.0:
                    speed_alerts = alert_svc.check_speed_anomalies(threshold_kmh=75.0)
                    for sa in speed_alerts:
                        alerts_to_broadcast.append(sa)
            except Exception:
                pass

            # Geofence checks with point-in-polygon and speed limits
            try:
                geo_svc = GeofenceService(session)
                geo_alerts = geo_svc.check_point_in_geofences(
                    lat=cam.latitude,
                    lng=cam.longitude,
                    plate_text=plate,
                    camera_id=cam.id,
                    speed=speed,
                    timestamp=now
                )
                if geo_alerts:
                    for ga in geo_alerts:
                        alerts_to_broadcast.append(ga)
            except Exception:
                pass

            return event_dict, alerts_to_broadcast, reid_upgrade

        finally:
            session.close()


# Singleton engine instance
traffic_streamer = CityTrafficStreamEngine(interval_seconds=4.0)
