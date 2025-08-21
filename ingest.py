# fogo_companion/ingest.py
import os
import re
from pathlib import Path
from utils import load_and_split_docs, compute_file_hash
from vectorstore import VectorStore

def ingest_directory(docs_dir, vectorstore, rebuild=False):
    """
    Ingest all docs in docs_dir into the vectorstore.
    If rebuild=True, clears index first.
    """
    docs_path = Path(docs_dir)
    if not docs_path.exists():
        raise FileNotFoundError(f"Docs directory not found: {docs_dir}")

    # Get all supported files recursively
    all_files = []
    supported_exts = ["*.pdf", "*.docx", "*.txt", "*.md"]
    for ext in supported_exts:
        all_files.extend(list(docs_path.rglob(ext)))

    if not all_files:
        print(f"[Ingest] No documents found in {docs_dir}")
        return

    if rebuild:
        print("[Ingest] Rebuilding index from scratch...")
        vectorstore.metadata = []
        vectorstore.save()

    for file_path in all_files:
        print(f"[Ingest] Processing {file_path}...")
        try:
            chunks = load_and_split_docs(str(file_path))
            if not chunks:
                print(f"[Ingest] No text extracted from {file_path}, skipping.")
                continue

            file_hash = compute_file_hash(str(file_path))
            
            # Create metadata for each chunk
            metadatas = [{
                "source": str(file_path),
                "text": chunk,
                "project": _extract_project_tag(file_path.name),
                "file_hash": file_hash
            } for chunk in chunks]

            vectorstore.add_texts(chunks, metadatas)
        except Exception as e:
            print(f"[Ingest] Failed to process {file_path}: {e}")

    print(f"[Ingest] Completed ingestion. Total docs in index: {len(vectorstore.metadata)}")


def _extract_project_tag(filename):
    """
    Extract a project tag from the filename (before extension).
    Example: 'fogoTestnet.txt' -> 'fogo'
    """
    name = Path(filename).stem.lower()
    # Simple logic: take the first part before a common separator
    tag = re.split(r'[_ -]', name)[0]
    return tag.strip()


def auto_ingest_if_empty(docs_dir, vectorstore):
    """
    If vectorstore index is empty OR docs_dir contents differ from index,
    run ingestion automatically.
    Returns:
        (added_files, removed_files) - both are lists of absolute paths
    """
    added_files = []
    removed_files = []

    try:
        docs_path = Path(docs_dir)
        if not docs_path.exists():
            print(f"Warning: Docs directory '{docs_dir}' not found. Skipping auto-ingest.")
            return [], []

        # Check for empty index first
        if vectorstore.index_is_empty():
            print("[Auto-Ingest] Vector store is empty. Performing initial ingestion...")
            ingest_directory(str(docs_dir), vectorstore, rebuild=True)
            # After ingest, all files are considered "added" for this run
            all_files = []
            supported_exts = ["*.pdf", "*.docx", "*.txt", "*.md"]
            for ext in supported_exts:
                all_files.extend(list(docs_path.rglob(ext)))
            return [str(p) for p in all_files], []

        # --- Detect changes using file hashes for existing index ---
        existing_sources = {m.get("source"): m.get("file_hash") for m in vectorstore.metadata}

        current_files = {}
        supported_exts = ["*.pdf", "*.docx", "*.txt", "*.md"]
        for ext in supported_exts:
            for file_path in docs_path.rglob(ext):
                current_files[str(file_path)] = compute_file_hash(str(file_path))

        # Files to add or update
        for src, fhash in current_files.items():
            if src not in existing_sources or existing_sources[src] != fhash:
                added_files.append(src)

        # Files to remove
        for src in list(existing_sources.keys()):
            if src not in current_files:
                removed_files.append(src)

        # Perform the updates if changes are detected
        if added_files or removed_files:
            print(f"[Auto-Ingest] Changes detected. Syncing vector store...")
            
            # Remove deleted files first
            if removed_files:
                print(f"  - Removing {len(removed_files)} file(s).")
                vectorstore.remove_by_sources(removed_files)

            # Add/update changed files
            if added_files:
                print(f"  - Adding/updating {len(added_files)} file(s).")
                for file_path_str in added_files:
                    # Remove old versions first to prevent duplicates
                    vectorstore.remove_by_sources([file_path_str])
                    # Now add the new version
                    ingest_directory(str(Path(file_path_str).parent), vectorstore, rebuild=False)

        return added_files, removed_files

    except Exception as e:
        print(f"[Auto-Ingest] An unexpected error occurred: {e}")
        return added_files, removed_files