from smart_home.replay import ReplayRegistry, ReplaySelectionError
from smart_home.sensor_encoder import SensorEncoderConfig, SensorStateEncoder

__all__ = [
    "MotionSensor",
    "PressureSensor",
    "ReplayRegistry",
    "ReplaySelectionError",
    "SensorEncoderConfig",
    "SensorStateEncoder",
    "SmartHomeSensorRig",
]


def __getattr__(name):
    if name in {"MotionSensor", "PressureSensor", "SmartHomeSensorRig"}:
        from smart_home.sensors import MotionSensor, PressureSensor, SmartHomeSensorRig

        return {
            "MotionSensor": MotionSensor,
            "PressureSensor": PressureSensor,
            "SmartHomeSensorRig": SmartHomeSensorRig,
        }[name]
    raise AttributeError(f"module 'smart_home' has no attribute {name!r}")
