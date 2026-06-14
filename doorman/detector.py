"""Camera access and MediaPipe face detection. Owns the OpenCV VideoCapture instance."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
import numpy as np


class CameraUnavailableError(Exception):
    """Raised when the camera cannot be opened or produces only black frames."""


@dataclass
class DetectionResult:
    faces_found: bool
    confidence: float
    bounding_boxes: list = field(default_factory=list)


class Detector:
    def __init__(self, fps: int, confidence_threshold: float) -> None:
        self._fps = fps
        self._confidence_threshold = confidence_threshold

        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            raise CameraUnavailableError("Could not open camera device 0")

        mp_face = mp.solutions.face_detection
        self._face_detection = mp_face.FaceDetection(
            model_selection=0,
            min_detection_confidence=confidence_threshold,
        )

        self._health_check()

    def _health_check(self) -> None:
        """Capture 10 frames; raise CameraUnavailableError if >80% are near-black."""
        black_count = 0
        total = 10
        for _ in range(total):
            ret, frame = self._cap.read()
            if not ret or frame is None or np.mean(frame) < 10:
                black_count += 1
            time.sleep(0.05)

        if black_count / total > 0.8:
            self._cap.release()
            raise CameraUnavailableError(
                f"Camera health check failed: {black_count}/{total} frames were black or empty"
            )

    def detect(self) -> DetectionResult:
        """Capture one frame and run MediaPipe face detection."""
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return DetectionResult(faces_found=False, confidence=0.0)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._face_detection.process(rgb)

        if not results.detections:
            return DetectionResult(faces_found=False, confidence=0.0)

        boxes = []
        best_confidence = 0.0
        for detection in results.detections:
            score = detection.score[0] if detection.score else 0.0
            best_confidence = max(best_confidence, score)
            bbox = detection.location_data.relative_bounding_box
            boxes.append({
                "xmin": bbox.xmin,
                "ymin": bbox.ymin,
                "width": bbox.width,
                "height": bbox.height,
            })

        return DetectionResult(
            faces_found=True,
            confidence=best_confidence,
            bounding_boxes=boxes,
        )

    def release(self) -> None:
        """Release the OpenCV camera handle."""
        self._cap.release()
        self._face_detection.close()
