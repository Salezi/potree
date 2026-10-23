"""Extract E57 spherical panoramas and poses without loading point clouds.

Usage: python -m portree_server.extract_bubbles input.e57 output_directory
"""
import json
import sys
from pathlib import Path

import pye57
import pye57.libe57 as lib


def struct(node):
    return node if isinstance(node, lib.StructureNode) else lib.StructureNode(node)


def value(node):
    for cls in (lib.FloatNode, lib.IntegerNode, lib.StringNode):
        try:
            obj = node if isinstance(node, cls) else cls(node)
            return obj.value()
        except (TypeError, RuntimeError, lib.E57Exception):
            continue
    raise ValueError("Unsupported scalar node")


def xyz(node, names):
    s = struct(node)
    return [float(value(s[name])) for name in names]


def extract(source: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    e57 = pye57.E57(str(source))
    images = e57.image_file.root()["images2D"]
    stations = []
    for index in range(images.childCount()):
        item = struct(images.get(index))
        try:
            rep = struct(item["sphericalRepresentation"])
            pose = struct(item["pose"])
            jpeg = rep["jpegImage"]
            blob = jpeg if isinstance(jpeg, lib.BlobNode) else lib.BlobNode(jpeg)
            length = blob.byteCount()
            filename = f"station_{index + 1:03d}.jpg"
            # pye57 BlobNode.read accepts a writable buffer and byte offset.
            with (dest / filename).open("wb") as file:
                offset = 0
                while offset < length:
                    size = min(4 * 1024 * 1024, length - offset)
                    buf = bytearray(size)
                    blob.read(buf, offset, size)
                    file.write(buf)
                    offset += size
            stations.append({
                "id": index + 1,
                "name": f"Estação {index + 1}",
                "image": filename,
                "position": xyz(pose["translation"], ("x", "y", "z")),
                "rotation": xyz(pose["rotation"], ("w", "x", "y", "z")),
                "width": int(value(rep["imageWidth"])),
                "height": int(value(rep["imageHeight"])),
            })
            print(f"Extraída {filename}: {length} bytes", flush=True)
        except Exception as exc:
            print(f"Falha na imagem {index + 1}: {exc}", file=sys.stderr, flush=True)
            raise
    (dest / "stations.json").write_text(
        json.dumps({"stations": stations}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Concluído: {len(stations)} panoramas", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Uso: python -m portree_server.extract_bubbles arquivo.e57 pasta_saida")
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
