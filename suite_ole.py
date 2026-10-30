"""Independent CFB version 3 stream container writer and olefile-based reader.

SPDX-License-Identifier: MIT
Format source: Microsoft [MS-CFB] sections 2.2--2.6, public specification.
Unknown stream payloads are preserved; directory allocation/metadata is rebuilt.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct
import tempfile

FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FAT = 0xFFFFFFFD
DIF = 0xFFFFFFFC


def read_streams(path) -> dict[str, bytes]:
    import olefile
    with olefile.OleFileIO(str(path)) as ole:
        return {"/".join(parts): ole.openstream(parts).read()
                for parts in ole.listdir(streams=True, storages=False)}


def write_streams(path, streams: dict[str, bytes]) -> None:
    """Atomically write arbitrary nested streams, including miniature streams."""
    entries = [{"name": "Root Entry", "type": 5, "children": [], "left": FREE,
                "right": FREE, "child": FREE, "start": END, "size": 0, "color": 1}]
    paths = {(): 0}
    for raw_path, data in streams.items():
        parts = tuple(raw_path.split("/")) if isinstance(raw_path, str) else tuple(raw_path)
        if not parts or any(not part or len(part.encode("utf-16le")) > 62 or
                            any(c in part for c in ":/\\!\0") for part in parts):
            raise ValueError(f"Invalid CFB stream path: {raw_path!r}")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Stream contents must be bytes")
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            leaf = depth == len(parts)
            if key in paths:
                if leaf or entries[paths[key]]["type"] != 1:
                    raise ValueError("Duplicate stream or stream/storage collision")
                continue
            parent = paths[key[:-1]]
            if any(entries[index]["name"].upper() == key[-1].upper()
                   for index in entries[parent]["children"]):
                raise ValueError("CFB names must be unique without regard to case")
            index = len(entries)
            paths[key] = index
            entries.append({"name": key[-1], "type": 2 if leaf else 1,
                            "children": [], "left": FREE, "right": FREE,
                            "child": FREE, "start": END, "size": len(data) if leaf else 0,
                            "data": bytes(data) if leaf else b"", "color": 1})
            entries[parent]["children"].append(index)

    for entry in entries:
        children = sorted(entry["children"], key=lambda i: (
            len(entries[i]["name"].encode("utf-16le")), entries[i]["name"].upper()))
        deepest = len(children).bit_length() - 1

        def tree(items, depth=0):
            if not items:
                return FREE
            middle = len(items) // 2
            node = items[middle]
            entries[node]["left"] = tree(items[:middle], depth + 1)
            entries[node]["right"] = tree(items[middle + 1:], depth + 1)
            entries[node]["color"] = 0 if depth == deepest and depth else 1
            return node

        entry["child"] = tree(children)

    sectors, chains = [], []

    def allocate(blob):
        if not blob:
            return END
        start = len(sectors)
        count = (len(blob) + 511) // 512
        sectors.extend(blob[i * 512:(i + 1) * 512].ljust(512, b"\0") for i in range(count))
        chains.append((start, count))
        return start

    miniature = bytearray()
    minifat = []
    for entry in entries[1:]:
        if entry["type"] != 2 or not entry["size"]:
            continue
        if entry["size"] < 4096:
            start = len(minifat)
            count = (entry["size"] + 63) // 64
            entry["start"] = start
            minifat.extend(range(start + 1, start + count))
            minifat.append(END)
            miniature.extend(entry["data"].ljust(count * 64, b"\0"))
        else:
            entry["start"] = allocate(entry["data"])
    entries[0]["start"] = allocate(bytes(miniature))
    entries[0]["size"] = len(miniature)
    mini_start = allocate(struct.pack("<" + "I" * len(minifat), *minifat).ljust(
        ((len(minifat) + 127) // 128) * 512, b"\xff")) if minifat else END
    mini_count = (len(minifat) + 127) // 128
    directory = bytearray()
    for entry in entries:
        record = bytearray(128)
        name = entry["name"].encode("utf-16le") + b"\0\0"
        record[:len(name)] = name
        struct.pack_into("<HBBIII", record, 64, len(name), entry["type"],
                         entry["color"], entry["left"], entry["right"], entry["child"])
        struct.pack_into("<IQ", record, 116, entry["start"], entry["size"])
        directory.extend(record)
    directory_start = allocate(directory)
    ordinary_count = len(sectors)
    fat_count, dif_count = 0, 0
    while True:
        new_fat = (ordinary_count + fat_count + dif_count + 127) // 128
        new_dif = max(0, (new_fat - 109 + 126) // 127)
        if (new_fat, new_dif) == (fat_count, dif_count):
            break
        fat_count, dif_count = new_fat, new_dif
    fat_ids = list(range(ordinary_count, ordinary_count + fat_count))
    dif_ids = list(range(ordinary_count + fat_count, ordinary_count + fat_count + dif_count))
    table = [FREE] * (fat_count * 128)
    for start, count in chains:
        for index in range(start, start + count):
            table[index] = index + 1 if index < start + count - 1 else END
    for index in fat_ids:
        table[index] = FAT
    for index in dif_ids:
        table[index] = DIF
    packed_fat = struct.pack("<" + "I" * len(table), *table)
    sectors.extend(packed_fat[i * 512:(i + 1) * 512] for i in range(fat_count))
    for index, sector in enumerate(dif_ids):
        chunk = fat_ids[109 + index * 127:109 + (index + 1) * 127]
        chunk += [FREE] * (127 - len(chunk))
        chunk.append(dif_ids[index + 1] if index + 1 < dif_count else END)
        sectors.append(struct.pack("<128I", *chunk))
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<9I", header, 40, 0, fat_count, directory_start, 0, 4096,
                     mini_start, mini_count, dif_ids[0] if dif_ids else END, dif_count)
    struct.pack_into("<109I", header, 76, *(fat_ids[:109] + [FREE] * max(0, 109 - fat_count)))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(header)
            for sector in sectors:
                output.write(sector)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


read_ole = read_streams
write_ole = write_streams
