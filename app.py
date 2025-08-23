# fogo_companion/app.py
import os
import json
import shutil
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine, text

from vectorstore import VectorStore
from ingest import auto_ingest_if_empty
from utils import load_projects, load_and_split_docs, fogo_chat_prompt
from doc_reasoner import build_context_for_query
from llm_runner import generate_answer

load_dotenv()

# --- Configuration ---
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "False") == "True"
PORT = int(os.getenv("PORT", "5001"))
SECRET_KEY = os.getenv("SECRET_KEY", "fogo-secret-key")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# --- Vercel Compatibility ---
IS_VERCEL = os.getenv("VERCEL") == "1"
# Use /tmp for writable storage on Vercel, which is the only writable directory
TMP_DIR = Path("/tmp") if IS_VERCEL else Path(__file__).parent

# --- Paths ---
APP_ROOT = Path(__file__).parent
DOCS_DIR = TMP_DIR / "docs"
PROJECTS_DIR = TMP_DIR / "projects"
VECTOR_DIR = TMP_DIR / "vector_store"
TOPICS_FILE = TMP_DIR / "topics.json"

# --- Database Setup ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set. Please configure it in Vercel.")
engine = create_engine(DATABASE_URL)

# --- App Initialization ---
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY

# --- Helper Functions for Vercel's Ephemeral Filesystem ---

def copy_initial_data_to_tmp():
    """On Vercel, copy read-only project data to the writable /tmp directory."""
    if IS_VERCEL:
        print("Vercel environment detected. Copying initial data to /tmp...")
        # Copy project definitions if they don't exist in /tmp
        if not PROJECTS_DIR.exists() and (APP_ROOT / "projects").exists():
            shutil.copytree(APP_ROOT / "projects", PROJECTS_DIR)
        # Create docs and vector_store directories if they don't exist
        DOCS_DIR.mkdir(exist_ok=True)
        VECTOR_DIR.mkdir(exist_ok=True)


def sync_db_to_local_tmp():
    """
    Synchronizes the database content (documents and topics) to the local /tmp directory.
    This ensures the vector store and file-based logic have the correct data for the current invocation.
    """
    print("Syncing database to /tmp directory...")
    with engine.connect() as conn:
        # Sync documents
        docs = conn.execute(text("SELECT filename, original_content FROM documents")).fetchall()
        DOCS_DIR.mkdir(exist_ok=True)
        for filename, original in docs:
            if original:
                (DOCS_DIR / filename).write_bytes(original)

        # Sync topics
        topics_row = conn.execute(text("SELECT content FROM topics WHERE id = 1")).fetchone()
        if topics_row and topics_row[0]:
            TOPICS_FILE.write_text(topics_row[0], encoding="utf-8")


def seed_database_if_empty():
    """Creates tables and seeds them with initial data from the project's 'docs' folder if the db is empty."""
    print("Checking if database needs to be seeded...")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS documents (
                filename TEXT PRIMARY KEY,
                original_content BYTEA,
                extracted_text TEXT,
                file_hash TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY,
                content TEXT
            )
        """))
        conn.commit()

        # Check if documents table is empty
        doc_count = conn.execute(text("SELECT COUNT(*) FROM documents")).scalar()
        if doc_count == 0 and (APP_ROOT / "docs").exists():
            print("Database is empty. Seeding documents...")
            for file_path in (APP_ROOT / "docs").iterdir():
                if file_path.is_file():
                    try:
                        filename = file_path.name
                        original = file_path.read_bytes()
                        extracted_chunks = load_and_split_docs(str(file_path))
                        extracted = '\n\n'.join(extracted_chunks)
                        file_hash = hashlib.sha256(original).hexdigest()
                        conn.execute(text("""
                            INSERT INTO documents (filename, original_content, extracted_text, file_hash)
                            VALUES (:filename, :original, :extracted, :hash)
                        """), {"filename": filename, "original": original, "extracted": extracted, "hash": file_hash})
                    except Exception as e:
                        print(f"Skipped seeding {file_path.name}: {e}")
            conn.commit()

        # Check if topics table is empty
        topic_count = conn.execute(text("SELECT COUNT(*) FROM topics")).scalar()
        if topic_count == 0 and (APP_ROOT / "topics.json").exists():
            print("Seeding topics...")
            content = (APP_ROOT / "topics.json").read_text(encoding="utf-8")
            conn.execute(text("INSERT INTO topics (id, content) VALUES (1, :content)"), {"content": content})
            conn.commit()


# --- Initial Setup on App Start ---
copy_initial_data_to_tmp()
seed_database_if_empty()
vs = VectorStore(str(VECTOR_DIR))

# --- Routes ---

def is_admin():
    """Check if the user is logged in as admin."""
    return session.get("authenticated")


@app.route("/")
def index():
    """Render the main chat page."""
    try:
        sync_db_to_local_tmp() # Ensure /tmp is fresh for this request
        projects = load_projects()
        topics = []
        if TOPICS_FILE.exists():
            try:
                topics = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Warning: could not load or parse topics.json: {e}")
        return render_template("index.html", projects=projects, topics=topics)
    except Exception as e:
        print(f"Error in index route: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/send_message", methods=["POST"])
def send_message():
    """Handle incoming user messages."""
    try:
        sync_db_to_local_tmp() # Ensure /tmp files match the database
        auto_ingest_if_empty(str(DOCS_DIR), vs) # Sync vector store with /tmp

        payload = request.get_json(force=True)
        user_message = payload.get("message", "").strip()
        project = payload.get("project")
        api_key = payload.get("api_key", "").strip()

        if not user_message:
            return jsonify({"response": "Please send a non-empty message."})
        if not api_key:
            return jsonify({"response": "Please provide your Fireworks AI API key."})
        if vs.index_is_empty():
            return jsonify({"response": "The knowledge base is empty. Please add documents."})

        # Build context and generate answer
        metadata_list = vs.metadata
        doc_context_blocks = build_context_for_query(metadata_list, user_message, project=project)
        prompt = fogo_chat_prompt(user_message, doc_context_blocks, project=project)

        print(f"[Fogo Companion] Generated Prompt for LLM (first 1000 chars): {prompt[:1000]}...")
        result = generate_answer(prompt, api_key)
        return jsonify({"response": result})
    except Exception as e:
        print(f"Error in send_message: {e}")
        return jsonify({"response": f"Sorry, an error occurred: {e}"}), 500


# --- Admin Routes ---

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not ADMIN_PASSWORD:
        return "Admin password not set in environment.", 403

    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('admin'))
        else:
            return render_template('admin.html', authenticated=False, error="Invalid password")

    if not is_admin():
        return render_template('admin.html', authenticated=False)

    # If authenticated, fetch data from the database
    with engine.connect() as conn:
        doc_files = [row[0] for row in conn.execute(text("SELECT filename FROM documents")).fetchall()]
        topics_row = conn.execute(text("SELECT content FROM topics WHERE id = 1")).fetchone()
        topics_content = topics_row[0] if topics_row else "[]"

    return render_template('admin.html', authenticated=True, files=doc_files, topics=topics_content)


@app.route('/admin/upload', methods=['POST'])
def upload_file():
    if not is_admin():
        return redirect(url_for('admin'))

    if 'file' not in request.files or request.files['file'].filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin'))

    file = request.files['file']
    filename = secure_filename(file.filename)
    original_content = file.read()
    file_hash = hashlib.sha256(original_content).hexdigest()

    # Create a temporary path to process the file
    temp_path = DOCS_DIR / filename
    temp_path.write_bytes(original_content)
    extracted_chunks = load_and_split_docs(str(temp_path))
    extracted_text = '\n\n'.join(extracted_chunks)
    os.remove(temp_path) # Clean up the temp file

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO documents (filename, original_content, extracted_text, file_hash)
            VALUES (:filename, :original, :extracted, :hash)
            ON CONFLICT (filename) DO UPDATE 
            SET original_content = :original, extracted_text = :extracted, file_hash = :hash
        """), {
            "filename": filename,
            "original": original_content,
            "extracted": extracted_text,
            "hash": file_hash
        })
        conn.commit()

    flash(f'File "{filename}" uploaded successfully.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/delete/<path:filename>', methods=['POST'])
def delete_file(filename):
    if not is_admin():
        return redirect(url_for('admin'))
    
    secure_name = secure_filename(filename)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM documents WHERE filename = :filename"), {"filename": secure_name})
        conn.commit()
    
    flash(f'File "{secure_name}" deleted successfully.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/file_content/<path:filename>', methods=['GET'])
def get_file_content(filename):
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT extracted_text FROM documents WHERE filename = :filename"),
            {"filename": secure_filename(filename)}
        ).fetchone()

    if row:
        return jsonify({"content": row[0]})
    else:
        return jsonify({"error": "File not found"}), 404


@app.route('/admin/update_file/<path:filename>', methods=['POST'])
def update_file(filename):
    if not is_admin():
        return redirect(url_for('admin'))

    content = request.form.get('content')
    if content is None:
        flash('No content provided', 'error')
        return redirect(url_for('admin'))

    with engine.connect() as conn:
        conn.execute(
            text("UPDATE documents SET extracted_text = :content WHERE filename = :filename"),
            {"content": content, "filename": secure_filename(filename)}
        )
        conn.commit()

    flash(f'File "{secure_filename(filename)}" updated successfully.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/save_topics', methods=['POST'])
def save_topics():
    if not is_admin():
        return redirect(url_for('admin'))

    topics_str = request.form.get('topics')
    try:
        json.loads(topics_str) # Validate JSON format
    except json.JSONDecodeError:
        flash('Invalid JSON format. Please provide a valid JSON array of strings.', 'error')
        return redirect(url_for('admin'))

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO topics (id, content) VALUES (1, :content)
            ON CONFLICT (id) DO UPDATE SET content = :content
        """), {"content": topics_str})
        conn.commit()

    flash('Topics updated successfully.', 'success')
    return redirect(url_for('admin'))