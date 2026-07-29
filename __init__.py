import os

# OpenCV ships with its OpenEXR codec disabled by default; several nodes in
# this pack and the companion Blender-Toolbox write .exr skydomes. Enable it
# before cv2 initializes the codec (it reads this lazily on first EXR I/O).
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
