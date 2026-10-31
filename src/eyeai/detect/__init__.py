"""Local detection (gap filling only) and capability discovery."""
from .capability import DetectionPlan, NativeCapability, plan_detection
from .coco import COCO_CLASSES
from .pipeline import FrameDetector, LocalDetectionPipeline
from .yolox import Detection, YoloxDetector

__all__ = [
    "COCO_CLASSES",
    "Detection",
    "DetectionPlan",
    "FrameDetector",
    "LocalDetectionPipeline",
    "NativeCapability",
    "YoloxDetector",
    "plan_detection",
]
