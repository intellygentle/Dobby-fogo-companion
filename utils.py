
# fogo_companion/utils.py
import os
import hashlib
import json
from typing import List
from pathlib import Path

# PDF
import pypdf

# DOCX
import docx


def compute_file_hash(path: str) -> str:
    """
    Compute SHA256 hash of a file's binary contents.
    Used to detect changes even if modification time is unchanged.
    """
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_text_from_pdf(path: str) -> str:
    texts = []
    with open(path, "rb") as fh:
        reader = pypdf.PdfReader(fh)
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
                texts.append(t)
            except Exception:
                continue
    return "\n".join(texts)


def extract_text_from_docx(path: str) -> str:
    doc = docx.Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text]
    return "\n".join(paragraphs)


def extract_text_from_textfile(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> List[str]:
    text = text.replace("\r\n", "\n")
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if "\n" in chunk:
            last_nl = chunk.rfind("\n")
            if last_nl > int(chunk_size * 0.4):
                end = start + last_nl
                chunk = text[start:end]
        chunks.append(chunk.strip())
        start = end - overlap
        if start < 0:
            start = 0
        if start >= len(text):
            break
    return chunks


def load_and_split_docs(path: str):
    """
    Load a document or image from path, extract text, and split into chunks.
    Returns a list of chunk strings.
    """
    path_lower = path.lower()
    text_content = ""

    try:
        if path_lower.endswith(".pdf"):
            text_content = extract_text_from_pdf(path)
        elif path_lower.endswith(".docx"):
            text_content = extract_text_from_docx(path)
        elif path_lower.endswith(".txt") or path_lower.endswith(".md"):
            text_content = extract_text_from_textfile(path)
        else:
            raise ValueError(f"Unsupported file type: {path}")
    except Exception as e:
        raise RuntimeError(f"Failed to read {path}: {e}")

    if not text_content.strip():
        return []

    return chunk_text(text_content)


def fogo_chat_prompt(user_question: str, context_texts: List[str], project: str = None) -> str:
    """
    Enhanced prompt for Fogo Companion:
    - Encourages varied, insightful, and reasoned responses.
    - Instructs the LLM to avoid repetitive phrasing and synthesize information creatively.
    """
    header = (
        "You are Fogo Companion, a highly intelligent assistant specializing in on-chain projects. "
        "Your goal is to provide clear, insightful, and varied answers by synthesizing internal documents and reasoning critically. "
        "Avoid repetitive phrasing and strive for unique, engaging expressions in each response."
    )
    if project:
        header += f" The user is asking about: {project}."

    ctx = "\n\n---\n\n".join(context_texts) if context_texts else "No relevant documents were found."

    instructions = """
INSTRUCTIONS FOR THE ASSISTANT:
1. **Synthesize and Reason**: Analyze the provided document snippets and reason step-by-step to craft a comprehensive answer. Connect concepts logically and provide insights that go beyond surface-level information.
2. **Avoid Repetition**: Do not reuse the same opening phrases or structures across responses. Craft each answer with fresh wording and perspectives, even if answering the same question multiple times.
3. **Be Insightful**: Highlight unique aspects of the project, such as technological advantages, real-world impact, or comparisons to competitors. Use critical thinking to infer implications or potential future developments.
4. **Evaluate Credibility**: Prioritize information from official or authoritative sources (e.g., whitepapers, official announcements) over less reliable ones. If sources conflict, analyze the discrepancy and provide a reasoned judgment.
5. **Structure Clearly**: Start with a direct, concise answer to the user's question, followed by a detailed explanation. Use bullet points, examples, or analogies where appropriate to enhance clarity and engagement.
6. **Preserve Exact Details**: When providing technical details (e.g., URLs, contract addresses, commands), reproduce them verbatim from the documents. Do not paraphrase or omit specifics.
7. **Professional Tone**: Maintain a professional, respectful tone. Avoid slang, profanity, or overly casual language unless explicitly relevant to the context.
8. **Handle Uncertainty**: If information is missing or unclear, acknowledge it and provide the best possible answer based on available data, suggesting where users can find more details (e.g., official websites).
9. **Comparative Analysis**: If relevant, compare the project to others, emphasizing unique features like performance, scalability, or solutions to blockchain challenges (e.g., MEV, latency).
10. **Engage the User**: Tailor the response to be engaging and actionable, encouraging exploration (e.g., visiting project websites or experimenting with tools).
"""

    prompt = (
        f"{header}\n\n{instructions}\n\n"
        f"DOCUMENT CONTEXT:\n{ctx}\n\n"
        f"USER QUESTION: {user_question}\n\n"
        "Now, provide a brilliant, varied, and insightful response, reasoning through the information to deliver a unique perspective."
    )
    return prompt


def discover_project_names(projects_dir: Path) -> List[str]:
    names = []
    if not projects_dir.exists():
        return names
    for f in projects_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = data.get("project") or data.get("name") or f.stem
            names.append(str(name))
        except Exception:
            names.append(f.stem)
    return names


def load_projects() -> dict:
    projects_path = Path("projects")
    projects = {}
    if not projects_path.exists():
        return projects
    for f in projects_path.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            key = data.get("project") or data.get("name") or f.stem
            projects[key] = data
        except Exception:
            continue
    return projects