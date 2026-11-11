"""Builds gnaf/gnaf.sqlite, an address lookup table, from the G-NAF zip.

    venv/bin/python gnaf_import.py gnaf/gnaf.zip [gnaf/gnaf.sqlite]

G-NAF is the Geocoded National Address File, from data.gov.au (the
"PSV" zip, GDA2020).  The pipe-separated tables are read straight from
the zip, staged in SQLite, joined into one `address` table, and the
staging tables dropped.  Each address has a key in the form map.py
searches for:

    29 HAY STREET LEICHHARDT
    2/29 HAY STREET LEICHHARDT
    12-14 GEORGE STREET NORTH ROCKS

G-NAF © Geoscape Australia, licensed under CC BY 4.0.
"""

import csv
import io
import os
import sqlite3
import sys
import time
import zipfile

# Staged tables: G-NAF table name -> columns kept.
STAGED = {
    "STATE": ["STATE_PID", "STATE_ABBREVIATION"],
    "LOCALITY": ["LOCALITY_PID", "LOCALITY_NAME", "STATE_PID"],
    "STREET_LOCALITY": ["STREET_LOCALITY_PID", "STREET_NAME",
                        "STREET_TYPE_CODE", "STREET_SUFFIX_CODE"],
    "ADDRESS_DETAIL": [
        "ADDRESS_DETAIL_PID", "DATE_RETIRED", "ALIAS_PRINCIPAL",
        "FLAT_NUMBER_PREFIX", "FLAT_NUMBER", "FLAT_NUMBER_SUFFIX",
        "LOT_NUMBER_PREFIX", "LOT_NUMBER", "LOT_NUMBER_SUFFIX",
        "NUMBER_FIRST_PREFIX", "NUMBER_FIRST", "NUMBER_FIRST_SUFFIX",
        "NUMBER_LAST_PREFIX", "NUMBER_LAST", "NUMBER_LAST_SUFFIX",
        "STREET_LOCALITY_PID", "LOCALITY_PID", "POSTCODE"],
    "ADDRESS_DEFAULT_GEOCODE": ["ADDRESS_DETAIL_PID", "LATITUDE", "LONGITUDE"],
    # Authority (code) tables: national, not per state.
    "STREET_TYPE_AUT": ["CODE", "NAME"],
    "STREET_SUFFIX_AUT": ["CODE", "NAME"],
}

BUILD_SQL = """
CREATE TABLE address (
    key TEXT NOT NULL,          -- '2/29 HAY STREET LEICHHARDT'
    state TEXT NOT NULL,        -- 'NSW'
    postcode TEXT NOT NULL,
    principal INTEGER NOT NULL, -- 1 for the principal address, 0 for an alias
    latitude REAL NOT NULL,
    longitude REAL NOT NULL
);

INSERT INTO address
SELECT
    -- flat: '2/'
    CASE WHEN a.FLAT_NUMBER <> ''
         THEN a.FLAT_NUMBER_PREFIX || a.FLAT_NUMBER || a.FLAT_NUMBER_SUFFIX || '/'
         ELSE '' END
    -- number: '29', '12-14', or 'LOT 7' when there's no street number
    || CASE WHEN a.NUMBER_FIRST <> ''
         THEN a.NUMBER_FIRST_PREFIX || a.NUMBER_FIRST || a.NUMBER_FIRST_SUFFIX
              || CASE WHEN a.NUMBER_LAST <> ''
                      THEN '-' || a.NUMBER_LAST_PREFIX || a.NUMBER_LAST || a.NUMBER_LAST_SUFFIX
                      ELSE '' END
         ELSE 'LOT ' || a.LOT_NUMBER_PREFIX || a.LOT_NUMBER || a.LOT_NUMBER_SUFFIX END
    -- street: 'HAY STREET', 'GEORGE STREET NORTH'
    || ' ' || s.STREET_NAME
    || CASE WHEN s.STREET_TYPE_CODE <> '' THEN ' ' || s.STREET_TYPE_CODE ELSE '' END
    || CASE WHEN s.STREET_SUFFIX_CODE <> ''
            THEN ' ' || COALESCE(sx.NAME, s.STREET_SUFFIX_CODE) ELSE '' END
    || ' ' || l.LOCALITY_NAME,
    st.STATE_ABBREVIATION,
    a.POSTCODE,
    a.ALIAS_PRINCIPAL = 'P',
    CAST(g.LATITUDE AS REAL),
    CAST(g.LONGITUDE AS REAL)
FROM ADDRESS_DETAIL a
JOIN ADDRESS_DEFAULT_GEOCODE g USING (ADDRESS_DETAIL_PID)
JOIN STREET_LOCALITY s USING (STREET_LOCALITY_PID)
JOIN LOCALITY l ON l.LOCALITY_PID = a.LOCALITY_PID
JOIN STATE st ON st.STATE_PID = l.STATE_PID
LEFT JOIN STREET_SUFFIX_AUT sx ON sx.CODE = s.STREET_SUFFIX_CODE
WHERE a.DATE_RETIRED = ''
  AND (a.NUMBER_FIRST <> '' OR a.LOT_NUMBER <> '');

CREATE INDEX address_key ON address (key);

-- Street-type abbreviations, for normalising searches: 'ST' -> 'STREET'.
CREATE TABLE street_type (abbreviation TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT OR IGNORE INTO street_type SELECT NAME, CODE FROM STREET_TYPE_AUT WHERE NAME <> CODE;
"""


def table_members(zf):
    """Maps each staged table name to its PSV files in the zip.

    Files are named for their state and table, 'NSW_ADDRESS_DETAIL_psv.psv',
    or are authority tables, 'Authority_Code_STREET_TYPE_AUT_psv.psv'."""
    members = {name: [] for name in STAGED}
    for filename in zf.namelist():
        base = os.path.basename(filename)
        if not base.endswith("_psv.psv"):
            continue
        stem = base[:-len("_psv.psv")]
        if stem.startswith("Authority_Code_"):
            table = stem[len("Authority_Code_"):]
        else:
            table = stem.split("_", 1)[1]
        if table in members:
            members[table].append(filename)
    return members


def stage(db, zf, name, files):
    columns = STAGED[name]
    db.execute(f"CREATE TABLE {name} ({', '.join(c + ' TEXT' for c in columns)})")
    insert = f"INSERT INTO {name} VALUES ({', '.join('?' * len(columns))})"
    count = 0
    for filename in files:
        with zf.open(filename) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""),
                                delimiter="|", quoting=csv.QUOTE_NONE)
            header = next(reader)
            index = [header.index(c) for c in columns]
            rows = ([row[i] for i in index] for row in reader)
            before = db.total_changes
            db.executemany(insert, rows)
            count += db.total_changes - before
    print(f"  {name}: {count:,} rows from {len(files)} files")


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__.split("\n\n")[1])
    zip_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) == 3 else os.path.join(
        os.path.dirname(zip_path), "gnaf.sqlite")

    tmp_path = db_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    db = sqlite3.connect(tmp_path)
    db.execute("PRAGMA journal_mode = OFF")
    db.execute("PRAGMA synchronous = OFF")

    start = time.monotonic()
    with zipfile.ZipFile(zip_path) as zf:
        members = table_members(zf)
        missing = [name for name, files in members.items() if not files]
        if missing:
            sys.exit(f"No files for {', '.join(missing)} in {zip_path}")
        print("Staging G-NAF tables")
        for name in STAGED:
            stage(db, zf, name, members[name])

    print("Indexing staged tables")
    db.execute("CREATE INDEX s1 ON ADDRESS_DEFAULT_GEOCODE (ADDRESS_DETAIL_PID)")
    db.execute("CREATE INDEX s2 ON STREET_LOCALITY (STREET_LOCALITY_PID)")
    db.execute("CREATE INDEX s3 ON LOCALITY (LOCALITY_PID)")

    print("Building address table")
    db.executescript(BUILD_SQL)
    for name in STAGED:
        db.execute(f"DROP TABLE {name}")
    db.commit()
    count = db.execute("SELECT count(*) FROM address").fetchone()[0]

    print("Compacting")
    db.execute("VACUUM")
    db.close()
    os.replace(tmp_path, db_path)
    print(f"{count:,} addresses in {db_path}, in {time.monotonic() - start:.0f}s")


if __name__ == "__main__":
    main()
