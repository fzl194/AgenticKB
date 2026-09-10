"""Transactional logical locations; document keys and stored objects never move."""
from psycopg.errors import UniqueViolation


async def lock_kb(conn, kb_id):
    cur = await conn.execute(
        "SELECT id FROM knowledge_bases WHERE id = %s AND status = 'active' FOR UPDATE",
        [kb_id],
    )
    if await cur.fetchone() is None:
        raise ValueError("knowledge base is no longer active")


async def lock_document(conn, document_id):
    cur = await conn.execute("SELECT kb_id FROM asset_documents WHERE id = %s", [document_id])
    row = await cur.fetchone()
    if row is None:
        return None
    await lock_kb(conn, row["kb_id"])
    cur = await conn.execute("SELECT * FROM asset_documents WHERE id = %s FOR UPDATE", [document_id])
    return await cur.fetchone()


async def assert_location_free(conn, kb_id, directory, name, excluding=None):
    cur = await conn.execute(
        """SELECT id FROM asset_documents WHERE kb_id = %s
           AND COALESCE(directory_path, '') = %s AND document_name = %s
           AND deleted_at IS NULL AND id IS DISTINCT FROM %s LIMIT 1""",
        [kb_id, directory or "", name, excluding],
    )
    if await cur.fetchone():
        raise UniqueViolation("a document with this name already exists in the target folder")


async def assert_directory_exists(conn, kb_id, directory):
    if not directory:
        return
    cur = await conn.execute("SELECT id FROM kb_folders WHERE kb_id = %s AND path = %s", [kb_id, directory])
    folder = await cur.fetchone()
    if folder is None:
        raise ValueError("target folder no longer exists")
    return folder["id"]


async def folder_path(conn, kb_id, folder_id):
    if folder_id is None:
        return ""
    cur = await conn.execute("SELECT path FROM kb_folders WHERE id = %s AND kb_id = %s", [folder_id, kb_id])
    row = await cur.fetchone()
    if row is None:
        raise ValueError("target folder no longer exists")
    return row["path"]


async def move_document(conn, document_id, target_folder_id):
    doc = await lock_document(conn, document_id)
    if doc is None or doc["deleted_at"] is not None:
        return None
    directory = await folder_path(conn, doc["kb_id"], target_folder_id)
    await assert_location_free(conn, doc["kb_id"], directory, doc["document_name"], document_id)
    cur = await conn.execute(
        "UPDATE asset_documents SET directory_path = %s, folder_id = %s WHERE id = %s RETURNING *",
        [directory, target_folder_id, document_id],
    )
    return dict(await cur.fetchone())


async def relocate_folder(conn, folder_id, kb_id, name, parent_id, expected_path):
    await lock_kb(conn, kb_id)
    cur = await conn.execute("SELECT * FROM kb_folders WHERE id = %s AND kb_id = %s", [folder_id, kb_id])
    folder = await cur.fetchone()
    if folder is None:
        return None
    old = folder["path"]
    if old != expected_path:
        raise ValueError("folder changed; refresh and try again")
    parent = await folder_path(conn, kb_id, parent_id)
    if parent == old or parent.startswith(old + "/"):
        raise ValueError("cannot move folder into its own subtree")
    new = f"{parent}/{name}" if parent else name
    cur = await conn.execute(
        "SELECT id FROM kb_folders WHERE kb_id = %s AND path = %s AND id <> %s",
        [kb_id, new, folder_id],
    )
    if await cur.fetchone():
        raise UniqueViolation("target folder already exists")
    # Literal prefix matching: '%' and '_' are valid folder names, not wildcards.
    cur = await conn.execute(
        """SELECT id, directory_path, document_name FROM asset_documents
           WHERE kb_id = %s AND deleted_at IS NULL
           AND (directory_path = %s OR starts_with(directory_path, %s))""",
        [kb_id, old, old + "/"],
    )
    for doc in await cur.fetchall():
        directory = new + doc["directory_path"][len(old):]
        await assert_location_free(conn, kb_id, directory, doc["document_name"], doc["id"])
    await conn.execute("UPDATE kb_folders SET name = %s, parent_id = %s WHERE id = %s", [name, parent_id, folder_id])
    await conn.execute(
        """UPDATE kb_folders SET path = %s || substr(path, %s)
           WHERE kb_id = %s AND (path = %s OR starts_with(path, %s))""",
        [new, len(old) + 1, kb_id, old, old + "/"],
    )
    await conn.execute(
        """UPDATE asset_documents SET directory_path = %s || substr(directory_path, %s),
             folder_id = (SELECT f.id FROM kb_folders f WHERE f.kb_id = asset_documents.kb_id
                          AND f.path = %s || substr(asset_documents.directory_path, %s))
           WHERE kb_id = %s AND (directory_path = %s OR starts_with(directory_path, %s))""",
        [new, len(old) + 1, new, len(old) + 1, kb_id, old, old + "/"],
    )
    cur = await conn.execute("SELECT * FROM kb_folders WHERE id = %s", [folder_id])
    return dict(await cur.fetchone())
