"""Extract scan XYZ/color/intensity to LAS. This is a geometry-only MVP."""
import sys
from pathlib import Path
import numpy as np
import pye57
import laspy

def main(source, dest):
    e57 = pye57.E57(str(source))
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    with laspy.open(str(dest), mode="w", header=header) as writer:
        for index in range(e57.scan_count):
            scan = e57.read_scan(index, intensity=True, colors=True, ignore_missing_fields=True)
            x, y, z = (np.asarray(scan["cartesian" + axis], dtype=np.float64) for axis in "XYZ")
            valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
            if not np.any(valid):
                continue
            # Use an offset near the first valid point to avoid LAS integer overflow.
            # LAS offsets must be fixed for the entire file; determine them from scan 0.
            if index == 0:
                header.offsets = np.array([x[valid][0], y[valid][0], z[valid][0]])
                writer.header.offsets = header.offsets
            for start in range(0, len(x), 1_000_000):
                sl = slice(start, start + 1_000_000)
                mask = valid[sl]
                if not np.any(mask):
                    continue
                points = laspy.ScaleAwarePointRecord.zeros(int(mask.sum()), header=writer.header)
                for axis, arr in zip("xyz", (x, y, z)):
                    setattr(points, axis, arr[sl][mask])
                for key, field in (("red", "colorRed"), ("green", "colorGreen"), ("blue", "colorBlue")):
                    if field in scan:
                        values = np.asarray(scan[field])[sl][mask]
                        setattr(points, key, np.clip(values * (65535 if np.nanmax(values) <= 1 else 1), 0, 65535).astype(np.uint16))
                writer.write_points(points)
            print(f"Processed station {index + 1}/{e57.scan_count}", flush=True)

if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
