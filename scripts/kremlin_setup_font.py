"""Original MIT Cyrillic bitmaps for VGA 8x16 cells (CP866).

Each hex byte is one independently drawn row, MSB on the left. Capitals
occupy rows 2..12, lowercase rows 5..12; descenders use rows 13..14.
No host fonts, external font files or runtime rasterization are involved.
"""

UPPER = (
    "38 6c c6 c6 c6 fe c6 c6 c6 c6 c6",  # А
    "fc c0 c0 c0 f8 cc c6 c6 c6 cc f8",  # Б
    "fc 66 66 66 7c 66 66 66 66 66 fc",  # В
    "fe c6 c0 c0 c0 c0 c0 c0 c0 c0 c0",  # Г
    "3e 36 36 36 36 36 36 36 36 7f 63",  # Д
    "fe 66 62 60 68 78 68 60 62 66 fe",  # Е
    "d6 d6 d6 7c 38 7c d6 d6 d6 d6 d6",  # Ж
    "7c c6 06 06 0c 38 0c 06 06 c6 7c",  # З
    "c6 c6 c6 ce de f6 e6 c6 c6 c6 c6",  # И
    "6c 38 00 c6 c6 ce de f6 e6 c6 c6",  # Й
    "c6 cc d8 f0 e0 f0 d8 cc c6 c6 c6",  # К
    "3e 36 36 36 36 36 36 36 36 66 c6",  # Л
    "c6 ee fe fe d6 c6 c6 c6 c6 c6 c6",  # М
    "c6 c6 c6 c6 c6 fe c6 c6 c6 c6 c6",  # Н
    "7c c6 c6 c6 c6 c6 c6 c6 c6 c6 7c",  # О
    "fe c6 c6 c6 c6 c6 c6 c6 c6 c6 c6",  # П
    "fc 66 66 66 66 7c 60 60 60 60 f0",  # Р
    "3c 66 c2 c0 c0 c0 c0 c0 c2 66 3c",  # С
    "fe d6 92 18 18 18 18 18 18 18 3c",  # Т
    "c6 c6 c6 c6 c6 7e 06 06 06 cc 78",  # У
    "18 7e db db db db db 7e 18 18 3c",  # Ф
    "c6 c6 6c 6c 38 38 38 6c 6c c6 c6",  # Х
    "cc cc cc cc cc cc cc cc cc fe 06",  # Ц
    "c6 c6 c6 c6 c6 7e 06 06 06 06 06",  # Ч
    "d6 d6 d6 d6 d6 d6 d6 d6 d6 d6 fe",  # Ш
    "d6 d6 d6 d6 d6 d6 d6 d6 d6 fe 06",  # Щ
    "e0 60 60 60 7c 66 66 66 66 66 7c",  # Ъ
    "c6 c6 c6 c6 f6 de ce ce ce de f6",  # Ы
    "c0 c0 c0 c0 fc c6 c6 c6 c6 c6 fc",  # Ь
    "78 cc 06 06 06 3e 06 06 06 cc 78",  # Э
    "ce db db db db fb db db db db ce",  # Ю
    "7e c6 c6 c6 c6 7e 36 66 c6 c6 c6",  # Я
)

LOWER = (
    "78 0c 0c 7c cc cc cc 76",  # а
    "0e 38 60 60 7c 66 66 66 66 66 3c",  # б (ascender)
    "fc 66 66 7c 66 66 66 fc",  # в
    "7e 66 60 60 60 60 60 60",  # г
    "3c 2c 2c 2c 2c 2c 7e 66 66",  # д
    "7c c6 c6 fe c0 c0 c6 7c",  # е
    "d6 d6 7c 38 7c d6 d6 d6",  # ж
    "7c c6 06 3c 06 06 c6 7c",  # з
    "c6 c6 ce de f6 e6 c6 c6",  # и
    "6c 38 00 c6 c6 ce de f6 e6 c6 c6",  # й
    "c6 cc d8 f0 d8 cc c6 c6",  # к
    "3e 36 36 36 36 36 66 c6",  # л
    "c6 ee fe d6 d6 c6 c6 c6",  # м
    "c6 c6 c6 fe c6 c6 c6 c6",  # н
    "7c c6 c6 c6 c6 c6 c6 7c",  # о
    "fe c6 c6 c6 c6 c6 c6 c6",  # п
    "dc 66 66 66 66 66 7c 60 60 60",  # р
    "7c c6 c0 c0 c0 c0 c6 7c",  # с
    "fe d6 92 18 18 18 18 3c",  # т
    "c6 c6 c6 c6 c6 7e 06 0c f8",  # у
    "18 18 18 7e db db db db db 7e 18 18 18",  # ф
    "c6 6c 38 38 38 38 6c c6",  # х
    "cc cc cc cc cc cc fe 06 06",  # ц
    "c6 c6 c6 c6 7e 06 06 06",  # ч
    "d6 d6 d6 d6 d6 d6 d6 fe",  # ш
    "d6 d6 d6 d6 d6 d6 fe 06 06",  # щ
    "e0 60 60 7c 66 66 66 7c",  # ъ
    "c6 c6 c6 f6 de ce de f6",  # ы
    "c0 c0 c0 fc c6 c6 c6 fc",  # ь
    "7c c6 06 3e 06 06 c6 7c",  # э
    "ce db db fb db db db ce",  # ю
    "7e c6 c6 c6 7e 36 66 c6",  # я
)


def glyphs():
    result = []
    for index, rows in enumerate(UPPER + LOWER):
        raster = bytes.fromhex(rows)
        top = 2 if index < 32 or index in (33, 41, 52) else 5
        result.append((b"\0" * top + raster).ljust(16, b"\0"))
    assert len(result) == 64 and all(len(g) == 16 for g in result)
    return result
