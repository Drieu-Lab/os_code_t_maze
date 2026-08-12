"""records - pure cores for animal record-keeping that bridge to the sibling
os_code_t_maze_resources repo: the canonical animal-id list, the daily
husbandry table, per-animal run state, and the note-tag vocabulary. Stdlib-only,
headless-tested. This package owns ``animals.resources_root()`` - the one
resolver for the sibling resources repo (env override + landmark default).
"""


class RecordsFileError(Exception):
    """A records file exists on disk but could not be read/parsed.

    The record readers are deliberately *best-effort*: a missing or unreadable
    file yields an empty table (``load_rows`` / ``load_records`` return ``[]``)
    so the rig still runs without the resources repo present. But a **write**
    (a Records Manager Save) first reads the current file and merges the edited
    rows into it. If that read silently returned empty for a file that is
    *present but unreadable*, the Save would overwrite it with only the edited
    rows - dropping every other row (the clobber described in bug
    ``#260810_RecordsIntegrity``). The write path therefore uses a *strict*
    reader (``load_rows_for_write`` / ``load_records_for_write``) that raises
    this instead of returning empty, so the Save can abort rather than destroy
    data. ``file absent`` is still returned as empty (creating a fresh file is
    safe); only ``present but unreadable`` raises.
    """
