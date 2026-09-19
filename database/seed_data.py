import datetime
import random
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import init_db, get_session, Camera, PlateEvent, Trajectory, FlaggedVehicle, Alert
from analytics.trajectory import TrajectoryEngine

# Bhubaneswar Smart City Surveillance Camera Network Registry
MOCK_CAMERAS = [
    {"id": 1, "name": "CAM-001", "location_name": "Rasulgarh Junction", "lat": 20.2917, "lng": 85.8643, "direction": "North → South", "status": "ACTIVE"},
    {"id": 2, "name": "CAM-002", "location_name": "Vani Vihar Square", "lat": 20.3018, "lng": 85.8491, "direction": "East → West", "status": "ACTIVE"},
    {"id": 3, "name": "CAM-003", "location_name": "Acharya Vihar", "lat": 20.2982, "lng": 85.8362, "direction": "North → South", "status": "ACTIVE"},
    {"id": 4, "name": "CAM-004", "location_name": "Jaydev Vihar", "lat": 20.3015, "lng": 85.8239, "direction": "East → West", "status": "ACTIVE"},
    {"id": 5, "name": "CAM-005", "location_name": "Khandagiri Square", "lat": 20.2586, "lng": 85.7865, "direction": "South → West", "status": "ACTIVE"},
    {"id": 6, "name": "CAM-006", "location_name": "Master Canteen Square", "lat": 20.2676, "lng": 85.8427, "direction": "Central Loop", "status": "ACTIVE"},
    {"id": 7, "name": "CAM-007", "location_name": "Kalpana Square", "lat": 20.2562, "lng": 85.8441, "direction": "South → North", "status": "ACTIVE"},
    {"id": 8, "name": "CAM-008", "location_name": "Chandrasekharpur", "lat": 20.3248, "lng": 85.8172, "direction": "South → North", "status": "ACTIVE"},
    {"id": 9, "name": "CAM-009", "location_name": "Patia Square", "lat": 20.3547, "lng": 85.8178, "direction": "South → North", "status": "ACTIVE"},
    {"id": 10, "name": "CAM-010", "location_name": "KIIT Square", "lat": 20.3562, "lng": 85.8190, "direction": "West → East", "status": "ACTIVE"},
    {"id": 11, "name": "CAM-011", "location_name": "Infocity Junction", "lat": 20.3585, "lng": 85.8115, "direction": "North → West", "status": "ACTIVE"},
    {"id": 12, "name": "CAM-012", "location_name": "Baramunda Bus Stand", "lat": 20.2798, "lng": 85.7925, "direction": "West → North", "status": "ACTIVE"},
    {"id": 13, "name": "CAM-013", "location_name": "Cuttack-Puri Road (Ravi Talkies)", "lat": 20.2505, "lng": 85.8475, "direction": "South → East", "status": "MAINTENANCE"}
]

# Camera-Specific Junction Speed Distributions (Bhubaneswar Traffic Dynamics)
JUNCTION_SPEED_PROFILES = {
    1: (18.0, 26.0),  # Rasulgarh (Heavy crawl chokepoint)
    2: (34.0, 43.0),  # Vani Vihar Square
    3: (39.0, 48.0),  # Acharya Vihar
    4: (22.0, 31.0),  # Jaydev Vihar (Congested junction)
    5: (58.0, 68.0),  # Khandagiri (Express Bypass)
    6: (28.0, 36.0),  # Master Canteen (Downtown Core)
    7: (43.0, 52.0),  # Kalpana (Heritage link)
    8: (48.0, 58.0),  # Chandrasekharpur (Boulevard)
    9: (38.0, 46.0),  # Patia Square (Tech Corridor)
    10: (32.0, 40.0), # KIIT Square (Campus Pedestrian)
    11: (62.0, 72.0), # Infocity (High-speed Expressway)
    12: (30.0, 38.0), # Baramunda (Bus Terminal)
}

# Every vehicle has a completely UNIQUE and DISTINCT trajectory corridor
SAMPLE_VEHICLES = [
    # 1. Flagship SIH Target (Rasulgarh -> Infocity IT Hub: 8 checkpoints)
    {"plate": "OD02AB1234", "type": "Car", "color": "Silver", "make": "Sedan", "route": [1, 2, 3, 4, 8, 9, 10, 11]},
    # 2. Motorcycle (Downtown to Baramunda via South Ring: 4 checkpoints)
    {"plate": "OD05XY9087", "type": "Motorcycle", "color": "Black", "make": "Bike", "route": [6, 7, 5, 12]},
    # 3. SUV (West to East Arterial Express: 5 checkpoints)
    {"plate": "OD02CA9999", "type": "Car", "color": "White", "make": "SUV", "route": [12, 4, 3, 2, 1]},
    # 4. City Bus (South-West Transit Ring: 4 checkpoints)
    {"plate": "OD33BT8833", "type": "Bus", "color": "Blue", "make": "City Bus", "route": [5, 12, 6, 7]},
    # 5. Heavy Truck (East Gateway to Central Goods Yard: 4 checkpoints)
    {"plate": "OD14AK7710", "type": "Truck", "color": "Yellow", "make": "Heavy Truck", "route": [1, 2, 3, 6]},
    # 6. Speed Anomaly Sports Car (North Expressway Sprint: 4 checkpoints)
    {"plate": "OD02EE8820", "type": "Car", "color": "Red", "make": "Sedan", "route": [8, 9, 10, 11], "speed_anomaly": True},
    # 7. Old Town to Central Commercial: 4 checkpoints
    {"plate": "DL01CA9999", "type": "Car", "color": "White", "make": "Sedan", "route": [7, 6, 3, 4]},
    # 8. Tech SEZ Northbound Cargo: 4 checkpoints
    {"plate": "MH12AB4325", "type": "Truck", "color": "Dark Gray", "make": "Truck", "route": [11, 10, 9, 8]},
    # 9. East Gateway to Heritage Old Town: 3 checkpoints
    {"plate": "WB12AB1234", "type": "Car", "color": "Silver", "make": "Hatchback", "route": [1, 6, 7]},
    # 10. Commercial Hub to University Campus: 4 checkpoints
    {"plate": "KA05MJ4012", "type": "Car", "color": "Black", "make": "SUV", "route": [4, 8, 9, 10]},
    # 11. Bypass Heavy Transport: 3 checkpoints
    {"plate": "HR26DQ5521", "type": "Truck", "color": "Brown", "make": "Truck", "route": [12, 5, 7]},
    # 12. Station to Terminal Intercity Bus: 4 checkpoints
    {"plate": "UP16BT8833", "type": "Bus", "color": "Green", "make": "Bus", "route": [6, 3, 4, 12]},
    # 13. Hit & Run Suspect (High-Speed Escape Southwards: 4 checkpoints)
    {"plate": "CH01BL3344", "type": "Car", "color": "White", "make": "Sedan", "route": [9, 8, 4, 3], "speed_anomaly": True},
    # 14. Westbound NH16 Commuter: 4 checkpoints
    {"plate": "TS09FA8080", "type": "Car", "color": "Blue", "make": "Sedan", "route": [2, 3, 4, 12]},
    # 15. Southwest to North Residential Transit: 4 checkpoints
    {"plate": "GJ01ZZ9090", "type": "Car", "color": "White", "make": "Sedan", "route": [5, 12, 4, 8]},
    # 16. IT Park to Central Commercial: 4 checkpoints
    {"plate": "TN07CK1212", "type": "Car", "color": "Red", "make": "Sedan", "route": [11, 10, 9, 4]},
    # 17. City Center to Cuttack Highway: 4 checkpoints
    {"plate": "DL3SCK4419", "type": "Motorcycle", "color": "Black", "make": "Bike", "route": [6, 3, 2, 1]},
    # 18. North to East Trunk Corridor: 6 checkpoints
    {"plate": "UP14DR1102", "type": "Car", "color": "Silver", "make": "Sedan", "route": [10, 9, 8, 4, 3, 2]},
    # 19. Eastbound Urban Shuttle: 3 checkpoints
    {"plate": "OD02K1988", "type": "Car", "color": "White", "make": "Hatchback", "route": [3, 2, 1]},
    # 20. Heritage to Western Terminal: 3 checkpoints
    {"plate": "OD07H4455", "type": "Car", "color": "Silver", "make": "Sedan", "route": [7, 6, 12]}
]

def seed_database(force_refresh: bool = False):
    engine = init_db()
    session = get_session(engine)

    existing_cams = session.query(Camera).count()
    if existing_cams > 0 and not force_refresh:
        print(f"Database already contains {existing_cams} cameras. Skipping initial seed.")
        session.close()
        return

    print("Populating Smart City Camera Network (Bhubaneswar)...")
    session.query(Alert).delete()
    session.query(Trajectory).delete()
    session.query(PlateEvent).delete()
    session.query(Camera).delete()
    session.query(FlaggedVehicle).delete()
    session.commit()

    camera_lookup = {}
    for cam_data in MOCK_CAMERAS:
        cam = Camera(
            id=cam_data["id"],
            name=cam_data["name"],
            location_name=cam_data["location_name"],
            latitude=cam_data["lat"],
            longitude=cam_data["lng"],
            direction=cam_data.get("direction", "North → South"),
            status=cam_data["status"]
        )
        session.add(cam)
        camera_lookup[cam_data["id"]] = cam
    session.commit()
    print(f"Inserted {len(MOCK_CAMERAS)} Surveillance Cameras.")

    # Seed Blacklist / Watchlist Vehicles
    blacklisted = [
        {"plate": "OD02AB1234", "reason": "Reported Stolen / Wanted in Armed Robbery Case (SIH Alert)"},
        {"plate": "CH01BL3344", "reason": "Repeat Speed Violator / Hit & Run Suspect"}
    ]
    for bv in blacklisted:
        session.add(FlaggedVehicle(
            plate_text=bv["plate"],
            reason=bv["reason"],
            active=1,
            flagged_at=datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        ))
    session.commit()
    print("Seeded Blacklisted Watchlist Vehicles.")

    print("Generating Chronological Vehicle Sightings & Plate Events...")
    now = datetime.datetime.utcnow()
    total_events = 0

    for v_idx, veh in enumerate(SAMPLE_VEHICLES):
        route = veh["route"]
        plate = veh["plate"]
        v_type = veh["type"]
        v_color = veh.get("color", "White")
        v_make = veh.get("make", "Sedan")
        is_anomaly = veh.get("speed_anomaly", False)
        
        # Stagger start time over last 6 hours
        start_delta_minutes = 320 - (v_idx * 15)
        current_time = now - datetime.timedelta(minutes=start_delta_minutes)
        
        for step, cam_id in enumerate(route):
            if cam_id not in camera_lookup or camera_lookup[cam_id].status != "ACTIVE":
                continue
                
            cam = camera_lookup[cam_id]
            if is_anomaly:
                speed = random.uniform(84.0, 94.0)
            else:
                low_s, high_s = JUNCTION_SPEED_PROFILES.get(cam_id, (35.0, 50.0))
                type_mod = -3.0 if v_type in ["Truck", "Bus"] else 0.0
                speed = max(16.0, random.uniform(low_s, high_s) + type_mod)
                
            conf = round(random.uniform(0.92, 0.99), 3)
            
            event = PlateEvent(
                camera_id=cam_id,
                plate_text=plate,
                confidence=conf,
                timestamp=current_time,
                vehicle_type=v_type,
                vehicle_color=v_color,
                make_model=v_make,
                direction=cam.direction or "Northbound",
                speed_estimate_kmh=round(speed, 1)
            )
            session.add(event)
            total_events += 1
            
            # Stagger time to next camera
            if is_anomaly:
                transit_minutes = random.randint(1, 2)
            elif speed < 30.0:
                transit_minutes = random.randint(5, 8)
            elif speed < 45.0:
                transit_minutes = random.randint(3, 6)
            else:
                transit_minutes = random.randint(2, 4)
            current_time += datetime.timedelta(minutes=transit_minutes)

    # Add realistic background city traffic across all cameras to establish differentiated traffic flows
    BACKGROUND_DENSITIES = {
        1: (28, (18.0, 26.0)),  # Rasulgarh: Heavy volume, crawl speed
        2: (18, (34.0, 42.0)),  # Vani Vihar: Medium-high volume
        3: (14, (38.0, 46.0)),  # Acharya Vihar: Medium volume
        4: (26, (21.0, 30.0)),  # Jaydev Vihar: Heavy volume, chokepoint
        5: (8,  (58.0, 68.0)),  # Khandagiri: Free flow bypass
        6: (20, (27.0, 35.0)),  # Master Canteen: High downtown volume
        7: (10, (42.0, 50.0)),  # Kalpana: Medium-low volume
        8: (12, (48.0, 56.0)),  # Chandrasekharpur: Medium volume
        9: (16, (38.0, 45.0)),  # Patia Square: Medium volume
        10: (14, (32.0, 40.0)), # KIIT Square: Medium volume, campus speed
        11: (7,  (62.0, 72.0)), # Infocity: High-speed expressway
        12: (18, (29.0, 37.0)), # Baramunda: Medium-high transit volume
    }

    bg_counter = 100
    for cam_id, (count, (min_sp, max_sp)) in BACKGROUND_DENSITIES.items():
        cam = camera_lookup.get(cam_id)
        if not cam or cam.status != "ACTIVE":
            continue
        for i in range(count):
            bg_counter += 1
            bg_plate = f"OD02BG{bg_counter:04d}"
            bg_time = now - datetime.timedelta(minutes=random.randint(10, 180))
            bg_speed = round(random.uniform(min_sp, max_sp), 1)
            bg_type = random.choice(["Car", "Car", "Car", "Motorcycle", "SUV", "Bus"])
            event = PlateEvent(
                camera_id=cam_id,
                plate_text=bg_plate,
                confidence=round(random.uniform(0.91, 0.98), 3),
                timestamp=bg_time,
                vehicle_type=bg_type,
                vehicle_color=random.choice(["White", "Silver", "Black", "Grey", "Red"]),
                make_model=bg_type,
                direction=cam.direction or "Northbound",
                speed_estimate_kmh=bg_speed
            )
            session.add(event)
            total_events += 1

    session.commit()
    print(f"Inserted {total_events} Plate Events with differentiated camera flows.")

    print("Rebuilding Trajectories using TrajectoryEngine...")
    traj_engine = TrajectoryEngine(session)
    rebuilt = traj_engine.rebuild_all_trajectories()
    print(f"Successfully generated {len(rebuilt)} Reconstructed Vehicle Trajectories.")

    # Generate initial alerts for speed anomalies, blacklisted detections, and geofence zones
    try:
        from backend.services.alert_service import AlertService
        alert_svc = AlertService(session)
        speed_alerts = alert_svc.check_speed_anomalies(threshold_kmh=75.0)
        print(f"Generated {len(speed_alerts)} Speed Anomaly Alert(s).")

        # Also check blacklist sightings
        for bv in blacklisted:
            recent_ev = session.query(PlateEvent).filter(PlateEvent.plate_text == bv["plate"]).order_by(PlateEvent.timestamp.desc()).first()
            if recent_ev:
                alert_svc.create_alert(
                    alert_type="FLAGGED_VEHICLE",
                    severity="CRITICAL",
                    message=f"🚨 BLACKLISTED VEHICLE DETECTED - Plate: {bv['plate']}, Camera: {recent_ev.camera.name} ({recent_ev.camera.location_name}), Time: {recent_ev.timestamp.strftime('%H:%M:%S')}, Reason: {bv['reason']}",
                    plate_text=bv["plate"],
                    camera_id=recent_ev.camera_id
                )
        print("Generated initial Blacklist Alert.")

        # Seed Smart City Geofence Zones
        from database.models import GeofenceZone
        existing_zones = session.query(GeofenceZone).count()
        if existing_zones == 0:
            default_zones = [
                {
                    "name": "Secretariat VIP Security Corridor",
                    "polygon": [[20.2620, 85.8380], [20.2720, 85.8390], [20.2710, 85.8490], [20.2610, 85.8470]],
                    "color": "#f43f5e",
                    "zone_type": "restricted",
                    "curfew_start": None,
                    "curfew_end": None,
                    "speed_limit": None
                },
                {
                    "name": "DAV Public School Safe Zone",
                    "polygon": [[20.2970, 85.8190], [20.3070, 85.8210], [20.3080, 85.8380], [20.2960, 85.8360]],
                    "color": "#38bdf8",
                    "zone_type": "school_zone",
                    "curfew_start": "07:00",
                    "curfew_end": "16:00",
                    "speed_limit": 30.0
                },
                {
                    "name": "Infocity High-Tech Curfew Zone",
                    "polygon": [[20.3480, 85.8080], [20.3620, 85.8100], [20.3600, 85.8250], [20.3460, 85.8230]],
                    "color": "#a855f7",
                    "zone_type": "curfew",
                    "curfew_start": "22:00",
                    "curfew_end": "05:00",
                    "speed_limit": None
                }
            ]
            import json
            for gz in default_zones:
                session.add(GeofenceZone(
                    name=gz["name"],
                    polygon_json=json.dumps(gz["polygon"]),
                    color=gz["color"],
                    zone_type=gz["zone_type"],
                    curfew_start=gz["curfew_start"],
                    curfew_end=gz["curfew_end"],
                    speed_limit=gz["speed_limit"],
                    active=1,
                    created_at=datetime.datetime.utcnow()
                ))
            session.commit()
            print("Seeded 3 Default Smart City Geofence Zones.")

            # Seed an initial geofence breach alert
            from backend.services.geofence_service import GeofenceService
            geo_svc = GeofenceService(session)
            geo_alerts = geo_svc.check_point_in_geofences(
                lat=20.2667, lng=85.8436, plate_text="OD02AB1234", camera_id=6
            )
            print(f"Generated {len(geo_alerts)} initial Geofence Breach Alert(s).")
    except Exception as ae:
        print(f"Alert/Geofence generation error: {ae}")

    session.close()

if __name__ == "__main__":
    seed_database(force_refresh=True)
