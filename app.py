# fogo_companion/app.py
import os
import json
import shutil
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

from vectorstore import VectorStore
from ingest import auto_ingest_if_empty
from utils import fogo_chat_prompt, load_projects
from doc_reasoner import build_context_for_query
from llm_runner import generate_answer
# from x_search import search_x

load_dotenv()

# --- Configuration ---
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "False") == "True"  # Default to False for production
PORT = int(os.getenv("PORT", "5001"))
SECRET_KEY = os.getenv("SECRET_KEY", "fogo-secret-key")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# --- Vercel Compatibility ---
# Check if running on Vercel
IS_VERCEL = os.getenv("VERCEL") == "1"
# Use /tmp for writable storage on Vercel, otherwise use local directories
TMP_DIR = Path("/tmp") if IS_VERCEL else Path(__file__).parent

# --- Paths ---
APP_ROOT = Path(__file__).parent
DOCS_DIR = TMP_DIR / "docs"
PROJECTS_DIR = TMP_DIR / "projects"
VECTOR_DIR = TMP_DIR / "vector_store"
TOPICS_FILE = TMP_DIR / "topics.json"

# Function to copy initial data to /tmp on Vercel
def copy_data_to_tmp():
    if IS_VERCEL:
        print("Vercel environment detected. Copying data to /tmp...")
        try:
            # Copy docs
            if not DOCS_DIR.exists() and (APP_ROOT / "docs").exists():
                shutil.copytree(APP_ROOT / "docs", DOCS_DIR)
            # Copy projects
            if not PROJECTS_DIR.exists() and (APP_ROOT / "projects").exists():
                shutil.copytree(APP_ROOT / "projects", PROJECTS_DIR)
            # Copy vector_store if it exists
            if not VECTOR_DIR.exists() and (APP_ROOT / "vector_store").exists():
                shutil.copytree(APP_ROOT / "vector_store", VECTOR_DIR)
            # Copy topics.json if it exists
            if not TOPICS_FILE.exists() and (APP_ROOT / "topics.json").exists():
                shutil.copy2(APP_ROOT / "topics.json", TOPICS_FILE)
        except Exception as e:
            print(f"Error copying data to /tmp: {e}")

# --- App Initialization ---
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY

# Copy data on startup if on Vercel
try:
    copy_data_to_tmp()
except Exception as e:
    print(f"Failed to copy data to /tmp: {e}")

# Ensure directories exist after copy
try:
    DOCS_DIR.mkdir(exist_ok=True)
    PROJECTS_DIR.mkdir(exist_ok=True)
    VECTOR_DIR.mkdir(exist_ok=True)
except Exception as e:
    print(f"Error creating directories: {e}")

# Initialize VectorStore
try:
    vs = VectorStore(str(VECTOR_DIR))
except Exception as e:
    print(f"Error initializing VectorStore: {e}")
    vs = None

# Initial sync of document index at startup
try:
    if vs:
        added, removed = auto_ingest_if_empty(str(DOCS_DIR), vs)
        if added or removed:
            print(f"[Startup Sync] Added: {len(added)}, Removed: {len(removed)}")
except Exception as e:
    print(f"Error during initial document sync: {e}")

def is_admin():
    """Check if the user is logged in as admin."""
    return session.get("authenticated")

@app.route("/")
def index():
    """Render the main chat page."""
    try:
        projects = load_projects()
        topics = []
        if TOPICS_FILE.exists():
            try:
                with open(TOPICS_FILE, "r", encoding="utf-8") as f:
                    topics = json.load(f)
            except Exception as e:
                print(f"Warning: could not load or parse topics.json: {e}")
        return render_template("index.html", projects=projects, topics=topics)
    except Exception as e:
        print(f"Error rendering index page: {e}")
        return jsonify({"error": "Internal server error rendering main page"}), 500

@app.route("/send_message", methods=["POST"])
def send_message():
    """Handle incoming user messages."""
    try:
        payload = request.get_json(force=True)
        user_message = payload.get("message", "").strip()
        project = payload.get("project")
        api_key = payload.get("api_key", "").strip()

        if not user_message:
            return jsonify({"response": "Please send a non-empty message."})

        if not api_key:
            return jsonify({"response": "Please provide your Fireworks AI API key."})

        # 1. Sync document index to ensure it's up-to-date
        if vs:
            auto_ingest_if_empty(str(DOCS_DIR), vs)
        else:
            return jsonify({"response": "Vector store initialization failed."})

        if vs.index_is_empty():
            return jsonify({"response": "The knowledge base is empty. Please add documents."})

        # 2. Build context from internal documents
        metadata_list = vs.metadata
        doc_context_blocks = build_context_for_query(metadata_list, user_message, project=project)

        # 3. Generate the final prompt for the LLM
        prompt = fogo_chat_prompt(user_message, doc_context_blocks, project=project)

        print("[Fogo Companion] Generated Prompt for LLM:")
        print(prompt[:1000] + "...")  # Log the start of the prompt for debugging

        # 4. Get the answer from the LLM
        result = generate_answer(prompt, api_key)
        return jsonify({"response": result})

    except Exception as e:
        print(f"Error in send_message: {e}")
        return jsonify({"response": f"Sorry, I encountered an error: {e}"}), 500

# --- Admin Routes ---
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    try:
        if not ADMIN_PASSWORD:
            error_msg = "Admin password is not set in the environment. Please set ADMIN_PASSWORD in your .env file."
            print(error_msg)
            return error_msg, 403

        if request.method == 'POST':
            if request.form.get('password') == ADMIN_PASSWORD:
                session['authenticated'] = True
                return redirect(url_for('admin'))
            else:
                print("Invalid admin password attempt")
                return render_template('admin.html', authenticated=False, error="Invalid password")

        if not is_admin():
            return render_template('admin.html', authenticated=False)

        # --- Admin is authenticated, show the panel ---
        doc_files = [f for f in os.listdir(DOCS_DIR) if os.path.isfile(os.path.join(DOCS_DIR, f))] if DOCS_DIR.exists() else []
        
        topics_content = ""
        if TOPICS_FILE.exists():
            try:
                with open(TOPICS_FILE, "r", encoding="utf-8") as f:
                    topics_content = f.read()
            except Exception as e:
                print(f"Error reading topics.json: {e}")

        return render_template('admin.html', authenticated=True, files=doc_files, topics=topics_content)

    except Exception as e:
        print(f"Error in admin route: {e}")
        return jsonify({"error": f"Internal server error in admin route: {e}"}), 500

@app.route('/admin/upload', methods=['POST'])
def upload_file():
    try:
        if not is_admin():
            return redirect(url_for('admin'))

        if 'file' not in request.files:
            flash('No file part', 'error')
            return redirect(url_for('admin'))

        file = request.files['file']
        if file.filename == '':
            flash('No selected file', 'error')
            return redirect(url_for('admin'))

        if file:
            filename = secure_filename(file.filename)
            save_path = DOCS_DIR / filename
            file.save(save_path)
            # Trigger re-ingest
            if vs:
                auto_ingest_if_empty(str(DOCS_DIR), vs)
            flash(f'File "{filename}" uploaded successfully.', 'success')

        return redirect(url_for('admin'))

    except Exception as e:
        print(f"Error in upload_file: {e}")
        flash(f'Error uploading file: {e}', 'error')
        return redirect(url_for('admin'))

@app.route('/admin/delete/<path:filename>', methods=['POST'])
def delete_file(filename):
    try:
        if not is_admin():
            return redirect(url_for('admin'))

        file_path = DOCS_DIR / secure_filename(filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            # Trigger re-ingest
            if vs:
                auto_ingest_if_empty(str(DOCS_DIR), vs)
            flash(f'File "{filename}" deleted successfully.', 'success')
        else:
            flash(f'File "{filename}" not found.', 'error')

        return redirect(url_for('admin'))

    except Exception as e:
        print(f"Error in delete_file: {e}")
        flash(f'Error deleting file: {e}', 'error')
        return redirect(url_for('admin'))

@app.route('/admin/file_content/<path:filename>', methods=['GET'])
def get_file_content(filename):
    try:
        if not is_admin():
            return jsonify({"error": "Unauthorized"}), 401

        file_path = DOCS_DIR / secure_filename(filename)
        if not os.path.exists(file_path):
            return jsonify({"error": "File not found"}), 404

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({"content": content})

    except Exception as e:
        print(f"Error in get_file_content: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/admin/update_file/<path:filename>', methods=['POST'])
def update_file(filename):
    try:
        if not is_admin():
            return redirect(url_for('admin'))

        content = request.form.get('content')
        if content is None:
            flash('No content provided', 'error')
            return redirect(url_for('admin'))

        file_path = DOCS_DIR / secure_filename(filename)
        if not os.path.exists(file_path):
            flash('File not found', 'error')
            return redirect(url_for('admin'))

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        # Trigger re-ingest
        if vs:
            auto_ingest_if_empty(str(DOCS_DIR), vs)
        flash(f'File "{filename}" updated successfully.', 'success')

        return redirect(url_for('admin'))

    except Exception as e:
        print(f"Error in update_file: {e}")
        flash(f'Error updating file: {e}', 'error')
        return redirect(url_for('admin'))

@app.route('/admin/save_topics', methods=['POST'])
def save_topics():
    try:
        if not is_admin():
            return redirect(url_for('admin'))

        topics_str = request.form.get('topics')
        json.loads(topics_str)  # Validate JSON
        with open(TOPICS_FILE, "w", encoding="utf-8") as f:
            f.write(topics_str)
        flash('Topics updated successfully.', 'success')

    except json.JSONDecodeError:
        flash('Invalid JSON format. Please provide a valid JSON array of strings.', 'error')
    except Exception as e:
        print(f"Error in save_topics: {e}")
        flash(f'An error occurred: {e}', 'error')

    return redirect(url_for('admin'))

# The following block is commented out as Vercel uses a WSGI server like Gunicorn
# to run the 'app' object, and does not execute this script directly.
# if __name__ == "__main__":
#     app.run(debug=FLASK_DEBUG, host="0.0.0.0", port=PORT)