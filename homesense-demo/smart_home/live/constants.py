CAMERA_PRESETS = {
    "overview": {"position": [0.0, -0.6, 13.5], "look_at": [0.0, -0.6, 0.0]},
    "robot": {"position": [2.4, -2.2, 1.4], "look_at": [0.45, -1.55, 0.7]},
    "resident": None,
}

CEILING_MODEL_IDS = {"gatymy", "kdovid", "kqvkod", "ssgfbx", "uidjxd", "wmhznm", "zhzvbw"}
CEILING_HIDDEN_CAMERA_MODES = {"overview"}

HUMAN_RADIUS_M = 0.24
HUMAN_MOVE_SPEED_MPS = 1.15
HUMAN_COMMAND_LIMIT_M = 0.12
VIEWPORT_INPUT_HOLD_S = 0.12
VIEWPORT_ROTATION_STEP_DEG = 15.0
VIEWPORT_CAMERA_KEYS = {
    "KEY_1": "overview",
    "KEY_2": "resident",
    "KEY_3": "robot",
}

OBSTACLE_MIN_HEIGHT_M = 0.16
DOOR_NAME_TOKENS = ("door", "doors", "doorknob", "doorframe")
MEROM_DOOR_OBJECT_NAMES = (
    "door_lvgliq_0",
    "door_lvgliq_1",
    "door_lvgliq_2",
    "door_lvgliq_3",
    "door_lvgliq_4",
)
OBSTACLE_IGNORE_NAMES = ("floor", "floors", "ceiling", "ceilings", "roof", "ground", *DOOR_NAME_TOKENS)
OBSTACLE_PATH_PREFIX = "/World/scene_0/"
MEROM_HUMAN_START_POS = (2.55, 8.55, 0.0)
MEROM_DOORLESS_PORTALS = (
    {"name": "door_lvgliq_0", "position": [1.230560302734375, 7.725292682647705], "radius_m": 0.72},
    {"name": "door_lvgliq_1", "position": [2.195575475692749, 4.063839435577393], "radius_m": 0.68},
    {"name": "door_lvgliq_2", "position": [2.859349012374878, 5.674045085906982], "radius_m": 0.72},
    {"name": "door_lvgliq_3", "position": [0.7042829990386963, 8.71489143371582], "radius_m": 0.82},
    {"name": "door_lvgliq_4", "position": [4.1243672370910645, 3.0857040882110596], "radius_m": 0.62},
)

MEROM_MOTION_SENSORS = [
    {
        "name": "motion_entry_door_wall",
        "zone": "entry_living",
        "position": [3.37, 9.74, 2.58],
        "yaw_deg": 0.0,
        "range_m": 1.55,
        "fov_deg": 360.0,
    },
    {
        "name": "motion_living_tv_console",
        "zone": "living_room",
        "position": [4.50, 9.03, 0.95],
        "yaw_deg": 180.0,
        "range_m": 2.65,
        "fov_deg": 62.0,
    },
    {
        "name": "motion_living_coffee_ceiling",
        "zone": "living_room",
        "position": [4.04, 7.43, 2.35],
        "yaw_deg": 0.0,
        "range_m": 2.0,
        "fov_deg": 360.0,
    },
    {
        "name": "motion_bath_left_wall",
        "zone": "bathroom",
        "position": [3.12, 5.25, 1.55],
        "yaw_deg": 0.0,
        "range_m": 1.95,
        "fov_deg": 76.0,
    },
    {
        "name": "motion_bedroom_entry_wall",
        "zone": "bedroom",
        "position": [4.65, 1.55, 1.65],
        "yaw_deg": 165.0,
        "range_m": 2.5,
        "fov_deg": 82.0,
    },
    {
        "name": "motion_bedroom_sofa_wall",
        "zone": "bedroom",
        "position": [4.90, 0.20, 1.60],
        "yaw_deg": -112.0,
        "range_m": 2.0,
        "fov_deg": 76.0,
    },
    {
        "name": "motion_utility_table_ceiling",
        "zone": "utility_room",
        "position": [-1.75, 0.25, 2.35],
        "yaw_deg": 0.0,
        "range_m": 0.85,
        "fov_deg": 360.0,
    },
    {
        "name": "motion_utility_laundry_ceiling",
        "zone": "utility_room",
        "position": [0.20, -1.10, 2.35],
        "yaw_deg": 0.0,
        "range_m": 0.85,
        "fov_deg": 360.0,
    },
]
