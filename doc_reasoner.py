# fogo_companion/doc_reasoner.py
import re
from collections import defaultdict
from typing import List, Dict, Tuple

# Heuristic patterns to detect procedure-like content and status claims
STEP_PATTERNS = [
    r'^\s*\d+\.', r'^\s*step\b', r'^\s*-\s', r'\bhow to\b', r'\binstructions?\b',
    r'\bexample\b', r'\bcommand\b', r'\bcurl\b', r'\brpc\b', r'\bendpoint\b',
    r'https?://', r'\bfaucet\b', r'\bgeth\b', r'\bsolana\b', r'\btransfer\b', r'\bwallet\b',
]

POSITIVE_STATUS_PATTERNS = [
    r'\bis\s+live\b', r'\bis\s+launched\b', r'\bis\s+available\b', r'\bis\s+open\b',
    r'\bis\s+active\b', r'\bnow\s+live\b', r'\bgo\s+live\b', r'\bpublic\s+mainnet\b',
]
NEGATIVE_STATUS_PATTERNS = [
    r'\bnot\s+live\b', r'\bnot\s+yet\b', r'\bcoming\s+soon\b', r'\bplanned\b',
    r'\bpermissioned\b', r'\bprivate\b', r'\brestricted\b', r'\bclosed\b', r'\bpaused\b',
]

def _count_matches(patterns: List[str], text: str) -> int:
    return sum(len(re.findall(p, text, flags=re.I | re.M)) for p in patterns)

def _normalize_text(s: str) -> str:
    return " ".join(s.split())

def analyze_documents(metadata_list: List[Dict]) -> Dict[str, Dict]:
    src_bucket = defaultdict(list)
    for meta in metadata_list:
        if meta.get("source") and meta.get("text"):
            src_bucket[meta["source"]].append(meta["text"])

    result = {}
    for src, texts in src_bucket.items():
        combined = "\n\n".join(texts)
        norm = _normalize_text(combined)
        result[src] = {
            "text": combined,
            "procedural_score": _count_matches(STEP_PATTERNS, combined),
            "positive_claims": _count_matches(POSITIVE_STATUS_PATTERNS, combined),
            "negative_claims": _count_matches(NEGATIVE_STATUS_PATTERNS, combined),
            "length": len(norm)
        }
    return result

def decide_authoritative_source(doc_signals: Dict[str, Dict]) -> Tuple[List[str], Dict]:
    if not doc_signals:
        return [], {}

    scores = []
    for src, sig in doc_signals.items():
        score = (sig.get("procedural_score", 0) * 10 + 
                 sig.get("length", 0) / 1000.0 + 
                 sig.get("positive_claims", 0) * 5 - 
                 sig.get("negative_claims", 0) * 2)
        scores.append((score, src, sig))

    scores.sort(reverse=True, key=lambda x: x[0])
    ordered_sources = [s for _, s, _ in scores]
    chosen = scores[0][2] if scores else {}
    chosen_src = scores[0][1] if scores else None
    return ordered_sources, {"source": chosen_src, **chosen} if chosen_src else {}

def build_context_for_query(metadata_list: List[Dict], user_question: str, project: str = None, max_snippet_chars: int = 1500) -> List[str]:
    """
    Builds prioritized context snippets from documents for the LLM.
    This version focuses on creating a clear, structured context from docs only.
    The X data will be passed to the LLM separately.
    """
    doc_signals = analyze_documents(metadata_list)
    if not doc_signals:
        return []

    ordered_sources, top_info = decide_authoritative_source(doc_signals)
    
    blocks = []
    summary_lines = [
        "RECONCILIATION SUMMARY (from Documents):",
        f"- Query: {user_question}",
    ]
    if top_info.get("source"):
        summary_lines.append(f"- Top authoritative document: {top_info.get('source')}")
        summary_lines.append(f"  (Procedural Score: {top_info.get('procedural_score',0)}, Length: {top_info.get('length',0)})")
    
    summary_lines.append("- Rule: Preferring documents with concrete instructions for 'how-to' guidance.")
    blocks.append("\n".join(summary_lines))

    # Add content from authoritative documents first
    for src in ordered_sources:
        text = doc_signals[src].get("text", "").strip()
        if not text:
            continue
        
        start = 0
        while start < len(text):
            snippet = text[start:start + max_snippet_chars]
            header = f"Source: {src}\n"
            blocks.append(header + "\n" + snippet)
            start += max_snippet_chars
            
    return blocks