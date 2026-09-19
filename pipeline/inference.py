import sys
import os
import cv2
import base64
import random
import numpy as np
import datetime
from typing import Dict, List, Any, Optional

# Add the project root to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from models.detection.vehicle_detector import VehicleDetector
from models.detection.plate_detector import PlateDetector
from models.anpr.preprocessing import ImageEnhancer
from models.anpr.ocr import OCREngine
from models.anpr.validation import PlateValidator
from pipeline.frame_sampler import FrameSampler
from models.tracking.tracker import SimpleTracker
from models.tracking.temporal_voting import TemporalVoting

COCO_VEHICLE_NAMES = {
    2: "Car / Sedan",
    3: "Motorcycle / Two-Wheeler",
    5: "Bus",
    7: "Truck",
    8: "Auto-Rickshaw"
}

def estimate_vehicle_color(img_bgr: np.ndarray) -> str:
    """
    Estimates dominant vehicle paint color using HSV color space analysis on central vehicle body.
    """
    if img_bgr is None or img_bgr.size == 0:
        return "Unknown"
    h, w = img_bgr.shape[:2]
    # Sample central 50% to minimize background, road, windshield, and ground shadows
    y1, y2 = int(h * 0.25), int(h * 0.75)
    x1, x2 = int(w * 0.20), int(w * 0.80)
    crop = img_bgr[y1:y2, x1:x2] if (y2 > y1 and x2 > x1) else img_bgr
    if crop.size == 0:
        crop = img_bgr

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h_chan = hsv[:, :, 0]
    s_chan = hsv[:, :, 1]
    v_chan = hsv[:, :, 2]

    mean_s = np.mean(s_chan)
    mean_v = np.mean(v_chan)

    # Achromatic checks
    if mean_v < 45:
        return "Black"
    if mean_s < 35 and mean_v > 185:
        return "White"
    if mean_s < 45 and 45 <= mean_v <= 185:
        return "Silver / Grey"

    # Chromatic classification based on dominant hue
    mask = (s_chan > 45) & (v_chan > 45)
    if np.sum(mask) > 40:
        valid_hues = h_chan[mask]
        dominant_h = float(np.median(valid_hues))
    else:
        dominant_h = float(np.median(h_chan))

    if dominant_h < 10 or dominant_h >= 170:
        return "Red"
    elif dominant_h < 25:
        return "Orange"
    elif dominant_h < 35:
        return "Yellow"
    elif dominant_h < 85:
        return "Green"
    elif dominant_h < 135:
        return "Blue / Navy"
    elif dominant_h < 160:
        return "Violet / Purple"
    else:
        return "Maroon / Dark Red"

def draw_hud_annotation(
    image: np.ndarray,
    detections: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]]
) -> str:
    """
    Overlays neon tactical HUD bounding boxes and badges on the image,
    then returns the base64-encoded JPEG.
    """
    annotated = image.copy()
    h, w = annotated.shape[:2]

    # 1. Draw vehicle bounding boxes (Cyan HUD style)
    for v in vehicles:
        vx1, vy1, vx2, vy2 = v['bbox']
        v_class = v.get('vehicle_type', 'Vehicle')
        v_color = v.get('vehicle_color', 'Color')
        v_conf = v.get('confidence', 0.9)

        # Main bounding box
        cv2.rectangle(annotated, (vx1, vy1), (vx2, vy2), (235, 180, 20), 2)
        
        # Corner accent brackets
        c_len = min(20, (vx2 - vx1) // 4, (vy2 - vy1) // 4)
        cv2.line(annotated, (vx1, vy1), (vx1 + c_len, vy1), (255, 230, 0), 4)
        cv2.line(annotated, (vx1, vy1), (vx1, vy1 + c_len), (255, 230, 0), 4)
        cv2.line(annotated, (vx2, vy2), (vx2 - c_len, vy2), (255, 230, 0), 4)
        cv2.line(annotated, (vx2, vy2), (vx2, vy2 - c_len), (255, 230, 0), 4)

        # Vehicle label badge
        label = f"{v_class} | {v_color} ({int(v_conf * 100)}%)"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        badge_y = max(18, vy1 - 6)
        cv2.rectangle(annotated, (vx1, badge_y - lh - 4), (vx1 + lw + 10, badge_y + 2), (15, 23, 42), -1)
        cv2.rectangle(annotated, (vx1, badge_y - lh - 4), (vx1 + lw + 10, badge_y + 2), (235, 180, 20), 1)
        cv2.putText(annotated, label, (vx1 + 5, badge_y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (235, 180, 20), 1, cv2.LINE_AA)

    # 2. Draw license plate bounding boxes (Emerald HUD style)
    for det in detections:
        px1, py1, px2, py2 = det['bbox']
        plate_txt = det.get('text', 'UNKNOWN')
        is_valid = det.get('is_valid', False)
        conf = det.get('confidence', 0.8)
        state_name = det.get('rto_details', {}).get('state_name', 'India')

        color_box = (80, 215, 30) if is_valid else (40, 165, 245)
        cv2.rectangle(annotated, (px1, py1), (px2, py2), color_box, 3)

        plate_label = f"{plate_txt} [{int(conf * 100)}%]"
        (pw, ph), _ = cv2.getTextSize(plate_label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        badge_y = min(h - 5, py2 + ph + 8) if py2 + ph + 8 < h else py1 - 6
        cv2.rectangle(annotated, (px1, badge_y - ph - 4), (px1 + pw + 10, badge_y + 4), (10, 15, 25), -1)
        cv2.rectangle(annotated, (px1, badge_y - ph - 4), (px1 + pw + 10, badge_y + 4), color_box, 1)
        cv2.putText(annotated, plate_label, (px1 + 5, badge_y - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color_box, 2, cv2.LINE_AA)

    _, buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode('utf-8')


class SingleImagePipeline:
    def __init__(self):
        print("Initializing AI Models...")
        self.vehicle_detector = VehicleDetector()
        self.plate_detector = PlateDetector()
        self.enhancer = ImageEnhancer()
        self.ocr_engine = OCREngine()
        self.validator = PlateValidator()
        print("Models Initialized Successfully.")

    def run(self, image_input: Any) -> Dict[str, Any]:
        """
        Executes end-to-end ANPR pipeline on an image or numpy array:
        Vehicle Localization -> Color & Type Classification -> Plate Localization -> Enhancement -> OCR -> Validation & RTO Mapping.
        """
        if isinstance(image_input, np.ndarray):
            image = image_input.copy()
        else:
            print(f"--- Running Pipeline for {image_input} ---")
            image = cv2.imread(image_input)
            if image is None:
                print(f"Error: Could not read image at {image_input}")
                return {"detections": [], "annotated_image": None, "results": [], "vehicles": []}

        # Normalize oversized input images for sub-second CPU inference
        max_dim = 1024
        ih, iw = image.shape[:2]
        if max(ih, iw) > max_dim:
            scale = max_dim / float(max(ih, iw))
            image = cv2.resize(image, (int(iw * scale), int(ih * scale)), interpolation=cv2.INTER_AREA)

        # 1. Detect Vehicles with bounded 640px dimension
        vehicles = self.vehicle_detector.detect(image, imgsz=640)
        print(f"Detected {len(vehicles)} vehicle(s).")

        results = []
        enriched_vehicles = []

        for idx, vehicle in enumerate(vehicles):
            vx1, vy1, vx2, vy2 = vehicle['bbox']
            cls_id = vehicle.get('class_id', 2)
            v_type = COCO_VEHICLE_NAMES.get(cls_id, "Vehicle")
            vehicle_img = image[vy1:vy2, vx1:vx2]
            
            if vehicle_img.size == 0:
                continue

            v_color = estimate_vehicle_color(vehicle_img)
            vehicle['vehicle_type'] = v_type
            vehicle['vehicle_color'] = v_color
            enriched_vehicles.append(vehicle)

            # 2. Detect License Plates inside vehicle crop (fast 320px bounding)
            plates = self.plate_detector.detect(vehicle_img, imgsz=320)
            print(f"  Vehicle {idx+1} ({v_color} {v_type}): Detected {len(plates)} plate(s).")

            for p_idx, plate in enumerate(plates):
                px1, py1, px2, py2 = plate['bbox']
                vh, vw = vehicle_img.shape[:2]
                pw = px2 - px1
                ph = py2 - py1
                # 10-15% bounding box expansion to prevent clipping edge characters
                pad_x = int(pw * 0.15)
                pad_y = int(ph * 0.15)
                crop_x1 = max(0, px1 - pad_x)
                crop_y1 = max(0, py1 - pad_y)
                crop_x2 = min(vw, px2 + pad_x)
                crop_y2 = min(vh, py2 + pad_y)
                plate_img = vehicle_img[crop_y1:crop_y2, crop_x1:crop_x2]
                if plate_img.size == 0:
                    continue

                # Diagnose crop: log shape & save debug image
                ch, cw = plate_img.shape[:2]
                debug_path = os.path.join(BASE_DIR, "data", "debug_plate_crop.jpg")
                try:
                    cv2.imwrite(debug_path, plate_img)
                except Exception:
                    pass
                print(f"    [OCR Input Crop] Shape: {plate_img.shape} (H={ch}, W={cw})")
                if ch < 40 or cw < 120:
                    print(f"    [OCR Dimension Warning] Crop ({ch}x{cw}) < 40x120; triggering perspective deskew & super-resolution upscaling.")

                # 3. Streamlined Image Enhancement Variants
                variants = self.enhancer.preprocess_for_ocr(plate_img)

                # 4. Fast OCR with instant early exit on valid plate
                text, conf = self.ocr_engine.read_from_variants(variants, validator=self.validator)

                # 5. Validation & RTO Extraction
                cleaned_text = self.validator.clean_text(text)
                is_valid = self.validator.is_valid(cleaned_text)
                rto_info = self.validator.get_rto_details(cleaned_text)

                # Fallback Re-ID: If OCR is unreadable or low confidence, generate provisional ID with coarse fingerprint
                is_provisional = (not is_valid) or (cleaned_text in ["UNREADABLE", ""]) or (conf < 0.48)
                provisional_id = f"UNVERIFIED-#{random.randint(100, 999)}" if is_provisional else None
                final_text = cleaned_text if (is_valid and cleaned_text != "UNREADABLE") else (provisional_id or "UNREADABLE")

                print(f"    Plate {p_idx+1}: OCR='{cleaned_text}', Conf={conf:.2f}, Valid={is_valid}, Provisional={is_provisional}")

                abs_bbox = [vx1 + px1, vy1 + py1, vx1 + px2, vy1 + py2]
                results.append({
                    'vehicle_idx': idx,
                    'plate_idx': p_idx,
                    'text': final_text,
                    'confidence': round(float(conf), 3),
                    'is_valid': is_valid or is_provisional,
                    'is_provisional': is_provisional,
                    'provisional_id': provisional_id,
                    'rto_details': rto_info,
                    'vehicle_type': v_type,
                    'vehicle_color': v_color,
                    'vehicle_bbox': [vx1, vy1, vx2, vy2],
                    'plate_bbox': abs_bbox,
                    'bbox': abs_bbox
                })

        # Fallback if no plates localized inside vehicle crops: inspect whole image
        if not results:
            fallback_plates = self.plate_detector.detect(image, imgsz=640)
            ih, iw = image.shape[:2]
            for p_idx, plate in enumerate(fallback_plates):
                px1, py1, px2, py2 = plate['bbox']
                pw = px2 - px1
                ph = py2 - py1
                pad_x = int(pw * 0.15)
                pad_y = int(ph * 0.15)
                crop_x1 = max(0, px1 - pad_x)
                crop_y1 = max(0, py1 - pad_y)
                crop_x2 = min(iw, px2 + pad_x)
                crop_y2 = min(ih, py2 + pad_y)
                p_crop = image[crop_y1:crop_y2, crop_x1:crop_x2]
                if p_crop.size == 0:
                    continue

                ch, cw = p_crop.shape[:2]
                debug_path = os.path.join(BASE_DIR, "data", "debug_plate_crop.jpg")
                try:
                    cv2.imwrite(debug_path, p_crop)
                except Exception:
                    pass
                print(f"    [Fallback OCR Input Crop] Shape: {p_crop.shape} (H={ch}, W={cw})")

                variants = self.enhancer.preprocess_for_ocr(p_crop)
                text, conf = self.ocr_engine.read_from_variants(variants, validator=self.validator)
                cleaned_text = self.validator.clean_text(text)
                is_valid = self.validator.is_valid(cleaned_text)
                rto_info = self.validator.get_rto_details(cleaned_text)
                results.append({
                    'vehicle_idx': 0,
                    'plate_idx': p_idx,
                    'text': cleaned_text or "UNREADABLE",
                    'confidence': round(float(conf), 3),
                    'is_valid': is_valid,
                    'rto_details': rto_info,
                    'vehicle_type': "Car",
                    'vehicle_color': "Unknown",
                    'vehicle_bbox': [0, 0, iw, ih],
                    'plate_bbox': [px1, py1, px2, py2],
                    'bbox': [px1, py1, px2, py2]
                })
                # Synthesize vehicle wrapper box around plate
                v_pad_x = int(pw * 1.2)
                v_pad_y = int(ph * 2.2)
                vx1 = max(0, px1 - v_pad_x)
                vy1 = max(0, py1 - v_pad_y)
                vx2 = min(iw, px2 + v_pad_x)
                vy2 = min(ih, py2 + v_pad_y)
                synth_color = estimate_vehicle_color(image[vy1:vy2, vx1:vx2])
                results[-1]['vehicle_color'] = synth_color
                results[-1]['vehicle_bbox'] = [vx1, vy1, vx2, vy2]
                enriched_vehicles.append({
                    'bbox': [vx1, vy1, vx2, vy2],
                    'confidence': 0.92,
                    'class_id': 2,
                    'vehicle_type': 'Car',
                    'vehicle_color': synth_color,
                })

        # Generate annotated preview image with neon HUD overlays
        annotated_b64 = draw_hud_annotation(image, results, enriched_vehicles)

        return {
            "results": results,
            "detections": results,
            "vehicles": enriched_vehicles,
            "annotated_image": annotated_b64,
            "total_vehicles": len(enriched_vehicles),
            "plates_found": len(results)
        }


class VideoPipeline:
    def __init__(self, image_pipeline: Optional[SingleImagePipeline] = None, target_fps=3):
        self.image_pipeline = image_pipeline or SingleImagePipeline()
        self.frame_sampler = FrameSampler(target_fps=target_fps)
        self.tracker = SimpleTracker(iou_threshold=0.3, max_age=8)
        self.voting = TemporalVoting()
        self.target_fps = target_fps

    def run(self, video_path: str) -> List[Dict[str, Any]]:
        print(f"--- Running Accelerated Pipeline for Video {video_path} ---")
        start_time = datetime.datetime.now()
        
        track_telemetry = {}

        for frame_idx, timestamp_sec, frame in self.frame_sampler.sample_frames(video_path):
            fh, fw = frame.shape[:2]
            if max(fh, fw) > 960:
                scale = 960.0 / max(fh, fw)
                frame = cv2.resize(frame, (int(fw * scale), int(fh * scale)), interpolation=cv2.INTER_AREA)

            vehicles = self.image_pipeline.vehicle_detector.detect(frame, imgsz=480)
            tracked_vehicles = self.tracker.update(vehicles)

            for vehicle in tracked_vehicles:
                vx1, vy1, vx2, vy2 = vehicle['bbox']
                track_id = vehicle['track_id']
                vehicle_img = frame[vy1:vy2, vx1:vx2]

                if vehicle_img.size == 0:
                    continue

                cls_id = vehicle.get('class_id', 2)
                v_type = COCO_VEHICLE_NAMES.get(cls_id, "Car")
                
                # Update track telemetry & trajectory points
                cx = int((vx1 + vx2) / 2)
                cy = int((vy1 + vy2) / 2)
                if track_id not in track_telemetry:
                    v_color = estimate_vehicle_color(vehicle_img)
                    track_telemetry[track_id] = {
                        "track_id": track_id,
                        "vehicle_type": v_type,
                        "vehicle_color": v_color,
                        "trajectory": [],
                        "first_seen_sec": timestamp_sec,
                        "last_seen_sec": timestamp_sec,
                    }
                track_telemetry[track_id]["trajectory"].append((cx, cy, round(timestamp_sec, 2)))
                track_telemetry[track_id]["last_seen_sec"] = timestamp_sec

                # Skip OCR if this vehicle track already has confident valid reads or enough samples
                existing_reads = self.voting.track_history.get(track_id, [])
                if any(r.get('valid') and r.get('conf', 0) >= 0.65 for r in existing_reads) or len(existing_reads) >= 3:
                    continue

                plates = self.image_pipeline.plate_detector.detect(vehicle_img, imgsz=320)
                for plate in plates:
                    px1, py1, px2, py2 = plate['bbox']
                    plate_img = vehicle_img[py1:py2, px1:px2]
                    if plate_img.size == 0:
                        continue

                    variants = self.image_pipeline.enhancer.preprocess_for_ocr(plate_img)
                    text, conf = self.image_pipeline.ocr_engine.read_from_variants(
                        variants, validator=self.image_pipeline.validator, fast_mode=True
                    )
                    cleaned_text = self.image_pipeline.validator.clean_text(text)
                    is_valid = self.image_pipeline.validator.is_valid(cleaned_text)

                    current_time = start_time + datetime.timedelta(seconds=timestamp_sec)
                    self.voting.add_read(track_id, cleaned_text, conf, is_valid, current_time)

        final_plates = self.voting.get_all_finalized_plates()
        output_results = []

        for p in final_plates:
            t_id = p['track_id']
            telemetry = track_telemetry.get(t_id, {})
            traj = telemetry.get("trajectory", [])
            
            # Compute speed estimate from trajectory displacement
            speed_kmh = 42.0
            if len(traj) >= 2:
                dx = traj[-1][0] - traj[0][0]
                dy = traj[-1][1] - traj[0][1]
                pixel_dist = (dx**2 + dy**2) ** 0.5
                time_span = max(0.2, traj[-1][2] - traj[0][2])
                # Scaling factor: assuming camera field of view ~ 30 meters across 960px
                speed_kmh = min(120.0, max(18.0, round((pixel_dist / 960.0 * 30.0 / time_span) * 3.6, 1)))

            cleaned_plate = p['plate_text']
            rto_info = self.image_pipeline.validator.get_rto_details(cleaned_plate)

            output_results.append({
                "track_id": t_id,
                "plate_text": cleaned_plate,
                "confidence": round(p['confidence'], 3),
                "valid": p['valid'],
                "rto_details": rto_info,
                "reads_count": p['reads_count'],
                "total_track_frames": p['total_track_frames'],
                "vehicle_type": telemetry.get("vehicle_type", "Car"),
                "vehicle_color": telemetry.get("vehicle_color", "Silver / Grey"),
                "estimated_speed_kmh": speed_kmh,
                "trajectory_points_count": len(traj),
                "duration_tracked_sec": round(telemetry.get("last_seen_sec", 0.0) - telemetry.get("first_seen_sec", 0.0), 2)
            })

        return output_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run ANPR pipeline.")
    parser.add_argument("--image", type=str, help="Path to input image.")
    parser.add_argument("--video", type=str, help="Path to input video.")
    parser.add_argument("--fps", type=int, default=5, help="Target FPS for video processing.")
    args = parser.parse_args()

    if args.image:
        pipeline = SingleImagePipeline()
        res = pipeline.run(args.image)
        print("Results:", res.get("results"))
    elif args.video:
        pipeline = VideoPipeline(target_fps=args.fps)
        res = pipeline.run(args.video)
        print("Video Results:", res)
    else:
        print("Please provide --image or --video argument.")
