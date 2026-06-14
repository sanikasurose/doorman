"""Entry point for the Doorman daemon. Day 1: minimal detection loop."""

from __future__ import annotations

import sys
import time

from doorman.detector import CameraUnavailableError, Detector

_FPS = 5
_CONFIDENCE_THRESHOLD = 0.6
_SLEEP = 1.0 / _FPS


def main() -> None:
    try:
        detector = Detector(fps=_FPS, confidence_threshold=_CONFIDENCE_THRESHOLD)
    except CameraUnavailableError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print("Doorman running — press Ctrl+C to stop")
    try:
        while True:
            result = detector.detect()
            print(result)
            time.sleep(_SLEEP)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        detector.release()


if __name__ == "__main__":
    main()
