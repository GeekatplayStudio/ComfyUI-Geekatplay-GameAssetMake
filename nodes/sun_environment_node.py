# =============================================================
# Geekatplay GameAssetMake — Sun / Environment
# (c) Geekatplay Studio / Vladimir Chopine
#
# Standalone sun + season control for workflows that don't use the
# full Scene Director (terrain-only, skydome-only, asset-only).
# Outputs the same environment_json the engine bridges consume, so
# Unreal/Unity set a DirectionalLight matching the season and hour.
# =============================================================
import json

from .scene_director_node import SEASONS, TIMES_OF_DAY, compute_sun


class SunEnvironmentNode:
    """
    Season + time of day (or explicit angles) -> environment_json for the bridges.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "season": (list(SEASONS.keys()), {"default": "summer"}),
                "time_of_day": (list(TIMES_OF_DAY.keys()), {"default": "afternoon"}),
                "mode": (["from season + time", "manual angles"],
                         {"default": "from season + time"}),
            },
            "optional": {
                "sun_azimuth_deg": ("FLOAT", {"default": 200.0, "min": 0.0, "max": 360.0, "step": 1.0,
                                              "tooltip": "0 = North, increasing clockwise (manual mode)."}),
                "sun_elevation_deg": ("FLOAT", {"default": 35.0, "min": -10.0, "max": 90.0, "step": 1.0,
                                                "tooltip": "0 = horizon, 90 = overhead (manual mode)."}),
                "intensity": ("FLOAT", {"default": 10.0, "min": 0.0, "max": 150.0, "step": 0.5}),
                "color_hex": ("STRING", {"default": "#FFF4E0"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("environment_json", "sun_summary")
    FUNCTION = "build_environment"
    CATEGORY = "Geekatplay GameAssetMake/Environment"

    def build_environment(self, season="summer", time_of_day="afternoon",
                          mode="from season + time", sun_azimuth_deg=200.0,
                          sun_elevation_deg=35.0, intensity=10.0, color_hex="#FFF4E0"):
        if mode == "manual angles":
            sun = {
                "azimuth_deg": round(float(sun_azimuth_deg), 1),
                "elevation_deg": round(float(sun_elevation_deg), 1),
                "intensity": float(intensity),
                "color_hex": color_hex or "#FFF4E0",
                "season": season,
                "time_of_day": time_of_day,
            }
        else:
            sun = compute_sun(season, time_of_day)

        environment = {"season": season, "time_of_day": time_of_day, "sun": sun}
        summary = (f"{season} {time_of_day}: sun azimuth {sun['azimuth_deg']}deg, "
                   f"elevation {sun['elevation_deg']}deg, intensity {sun['intensity']}, "
                   f"color {sun['color_hex']}")
        print(f"[GameAssetMake Sun] {summary}")
        return (json.dumps(environment, indent=2), summary)
