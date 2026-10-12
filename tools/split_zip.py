"""Split a big zip into zips of at most N MB, for sending somewhere with a size limit (chat attachments: 30 MB).

    python tools/split_zip.py capture.zip          # parts of at most 25 MB, next to the zip
    python tools/split_zip.py capture.zip 20       # at most 20 MB

Every file is kept whole, with its name, and goes in exactly one part (name_part01.zip, name_part02.zip, ...),
in the order it has in the original. A file bigger than the limit gets a part to itself, with a warning. Nothing
is compressed again: the files are copied as they are."""
import os
import sys
import zipfile


def split_zip(path: str, limit_mb: float = 25.0) -> list[str]:
    limit = int(limit_mb * 1024 * 1024)
    base = os.path.splitext(path)[0]
    parts: list[list[zipfile.ZipInfo]] = [[]]
    used = 0
    with zipfile.ZipFile(path) as source:
        for info in source.infolist():
            if info.is_dir():
                continue
            if info.file_size > limit:
                print(f"warning: {info.filename} is {info.file_size / 1048576:.1f} MB, over the limit - it gets a part to itself")
            if parts[-1] and used + info.file_size > limit:
                parts.append([])
                used = 0
            parts[-1].append(info)
            used += info.file_size
        paths = []
        for number, infos in enumerate(parts, 1):
            out = f"{base}_part{number:02d}.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as target:
                for info in infos:
                    target.writestr(info.filename, source.read(info))
            paths.append(out)
    return paths


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for made in split_zip(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 25.0):
        print(f"{made}  ({os.path.getsize(made) / 1048576:.1f} MB)")
