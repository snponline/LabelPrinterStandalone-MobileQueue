"""Lightweight local grounding for the AI ช่วยค้นข้อมูล feature.

No NotebookLM dependency - NotebookLM has no stable public API for a
shop-level account (only an enterprise-tier API, plus unofficial
reverse-engineered wrappers that can break anytime Google changes an
internal endpoint). Instead the pharmacist drops .txt/.md/.pdf reference
files into the knowledge folder; this module does plain keyword retrieval
over them locally - no LLM call, no network call, effectively free - and
returns only the few relevant paragraphs to attach to the AI prompt. This
keeps token cost low (a short snippet, not whole documents) and grounds the
answer instead of relying purely on the model's own knowledge.
"""
import os
import re

from storage import APP_DATA_DIR

KNOWLEDGE_DIR = os.path.join(APP_DATA_DIR, "knowledge")

# Re-scanned only when the folder's file list/mtimes change - PDFs in
# particular are slow-ish to re-extract every keystroke, so this cache is
# keyed on a signature of (filename, mtime) pairs rather than reloading
# unconditionally on every search.
_cache = {"signature": None, "docs": []}


def ensure_knowledge_dir():
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    readme_path = os.path.join(KNOWLEDGE_DIR, "README.txt")
    if not os.path.isfile(readme_path) and not os.listdir(KNOWLEDGE_DIR):
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(
                "วางไฟล์ .txt, .md, .pdf หรือ .docx ที่มีข้อมูลยา/โรคที่เชื่อถือได้ไว้ในโฟลเดอร์นี้\n"
                "ระบบจะค้นหาย่อหน้าที่เกี่ยวข้องจากไฟล์เหล่านี้มาแนบให้ AI ใช้ตอบคำถามอัตโนมัติ "
                "(ค้นในเครื่อง ไม่ส่งไฟล์ทั้งหมดออกไปไหน แนบแค่ย่อหน้าที่เกี่ยวข้องเท่านั้น)\n"
            )


def _extract_pdf_text(path):
    import fitz
    doc = fitz.open(path)
    try:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count))
    finally:
        doc.close()


def _extract_docx_text(path):
    import docx
    d = docx.Document(path)
    return "\n\n".join(p.text for p in d.paragraphs if p.text.strip())


def _all_files():
    # Recursive - the pharmacist can organize into subfolders (e.g.
    # ข้อมูลยา/, โรคผิวหนัง/) for their own sanity when managing files; the
    # search itself doesn't care about folder structure, it just needs to
    # find everything under KNOWLEDGE_DIR regardless of depth.
    for root, _dirs, files in os.walk(KNOWLEDGE_DIR):
        for fname in files:
            yield os.path.join(root, fname)


def _folder_signature():
    files = sorted(_all_files())
    return tuple((f, os.path.getmtime(f)) for f in files if os.path.isfile(f))


def _load_documents():
    signature = _folder_signature()
    if signature == _cache["signature"]:
        return _cache["docs"]

    docs = []
    for path in _all_files():
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        if name == "README.txt":
            continue
        # Relative path (e.g. "โรคผิวหนัง/ผื่นแพ้.txt") so a citation shows
        # which category a snippet came from, not just the bare filename.
        source = os.path.relpath(path, KNOWLEDGE_DIR).replace(os.sep, "/")
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".txt", ".md"):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            elif ext == ".pdf":
                text = _extract_pdf_text(path)
            elif ext == ".docx":
                text = _extract_docx_text(path)
            else:
                continue
        except Exception:
            continue
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if len(para) >= 20:
                docs.append((source, para))

    _cache["signature"] = signature
    _cache["docs"] = docs
    return docs


def _bigrams(text):
    # Character bigrams, not word tokens - Thai text has no reliable word
    # boundaries without a segmenter library (pythainlp etc.), and a query
    # like "มีตุ่มน้ำใส" typed as one run-on phrase would never exact-match
    # a source document that phrases it "เป็นตุ่มน้ำใส". Overlapping
    # 2-character shingles sidestep that without adding a heavy dependency.
    cleaned = re.sub(r"\s+", "", text.lower())
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def search_knowledge(query, top_k=3, max_chars=800, min_score=0.12):
    """Bigram-overlap scoring - no embeddings, no AI call, no network call.
    Good enough for short symptom queries against a modest local document
    set; if the knowledge folder grows large and this stops being accurate
    enough, that's the point to revisit with real embeddings."""
    ensure_knowledge_dir()
    docs = _load_documents()
    if not docs:
        return []

    query_grams = _bigrams(query)
    if not query_grams:
        return []

    scored = []
    for source, para in docs:
        para_grams = _bigrams(para)
        overlap = len(query_grams & para_grams)
        score = overlap / len(query_grams)
        if score >= min_score:
            scored.append((score, source, para))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"source": source, "snippet": para[:max_chars]} for _, source, para in scored[:top_k]]


def format_context_block(results):
    if not results:
        return ""
    parts = [
        "ข้อมูลอ้างอิงที่เกี่ยวข้อง (จากเอกสารที่ร้านเตรียมไว้ - ให้ใช้ข้อมูลนี้เป็นหลักในการตอบ "
        "ถ้าไม่พบข้อมูลที่ตรงประเด็นในนี้ ให้บอกตามตรงว่าไม่พบในเอกสารที่มี แล้วค่อยตอบจากความรู้ทั่วไป "
        "พร้อมระบุชัดเจนว่าไม่ได้อ้างอิงจากเอกสาร):\n"
    ]
    for r in results:
        parts.append(f"--- จาก {r['source']} ---\n{r['snippet']}\n")
    return "\n".join(parts)
