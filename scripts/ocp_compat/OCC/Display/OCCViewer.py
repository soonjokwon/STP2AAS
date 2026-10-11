class Viewer3d:  # offscreen rendering unavailable in the shim -> D6 placeholder fallback
    def __init__(self, *a, **k):
        raise RuntimeError("offscreen OpenGL viewer unavailable in OCP shim")
