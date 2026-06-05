import json
import logging
import os
import re
import sys

import chromadb
import ollama

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAG_SOURCE_FILE = "malumot.txt"
COLLECTION_NAME = "legal_rag_storage"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("ingest.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Ingest_Logger")


def normalize_source_item(item: dict, index: int) -> dict | None:
    if not isinstance(item, dict):
        return None

    article = str(item.get("article") or item.get("id") or f"Hujjat {index}").strip()
    title = str(item.get("title") or "").strip()

    if isinstance(item.get("paragraphs"), list):
        content = "\n".join(str(paragraph).strip() for paragraph in item["paragraphs"] if str(paragraph).strip())
    else:
        content = str(item.get("content") or item.get("text") or "").strip()

    if title and title.lower() not in article.lower():
        article = f"{article}. {title}"

    if not article or not content:
        return None

    return {
        "id": str(item.get("id") or item.get("article") or index).strip(),
        "article": article,
        "content": content,
        "source": RAG_SOURCE_FILE,
    }


def parse_plain_text_documents(raw_text: str) -> list[dict]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", raw_text) if block.strip()]
    if not blocks and raw_text.strip():
        blocks = [raw_text.strip()]

    documents = []
    for index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        title = lines[0].strip() if lines else f"Hujjat {index}"
        content = "\n".join(lines[1:]).strip() if len(lines) > 1 else block.strip()
        documents.append({
            "id": str(index),
            "article": title or f"Hujjat {index}",
            "content": content,
            "source": RAG_SOURCE_FILE,
        })
    documents.sort(key=lambda document: int(document_lookup_key(document)) if document_lookup_key(document).isdigit() else 10**9)
    return documents


def parse_json_objects_from_text(raw_text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    objects = []
    position = 0

    while position < len(raw_text):
        start = raw_text.find("{", position)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(raw_text[start:])
        except json.JSONDecodeError:
            position = start + 1
            continue

        if isinstance(value, dict):
            objects.append(value)
        position = start + end

    return objects


def decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def parse_structured_malumot_documents(raw_text: str) -> list[dict]:
    article_pattern = re.compile(r'"article"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"', re.DOTALL)
    article_matches = list(article_pattern.finditer(raw_text))
    documents = []

    for index, article_match in enumerate(article_matches, start=1):
        block_start = max(raw_text.rfind("{", 0, article_match.start()), 0)
        block_end = article_matches[index].start() if index < len(article_matches) else len(raw_text)
        body = raw_text[block_start:block_end]
        title_match = re.search(r'"title"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"', body, re.DOTALL)
        paragraphs_match = re.search(r'"paragraphs"\s*:\s*\[(?P<value>.*?)\n\s*\]', body, re.DOTALL)
        if not article_match or not paragraphs_match:
            continue

        paragraphs_raw = paragraphs_match.group("value")
        try:
            paragraphs = json.loads(f"[{paragraphs_raw}]")
        except json.JSONDecodeError:
            paragraphs = [
                decode_json_string(value)
                for value in re.findall(r'"((?:\\.|[^"\\])*)"', paragraphs_raw, flags=re.DOTALL)
            ]

        document = normalize_source_item({
            "id": index,
            "article": decode_json_string(article_match.group("value")),
            "title": decode_json_string(title_match.group("value")) if title_match else "",
            "paragraphs": paragraphs,
        }, index)
        if document:
            documents.append(document)

    return documents


def document_lookup_key(document: dict) -> str:
    article = document.get("article", "").lower()
    for apostrophe in ("’", "‘", "ʻ", "ʼ", "`", "´", "ʹ"):
        article = article.replace(apostrophe, "'")
    match = re.search(r"\b(\d+)\s*[-–—]?\s*modda\b", article)
    if match:
        return match.group(1)
    return re.sub(r"\s+", " ", article).strip()


def load_rag_source_documents() -> list[dict]:
    if not os.path.exists(RAG_SOURCE_FILE):
        logger.error(f"❌ '{RAG_SOURCE_FILE}' topilmadi! Jarayon to'xtatildi.")
        return []

    with open(RAG_SOURCE_FILE, "r", encoding="utf-8") as file:
        raw_text = file.read()

    documents = []
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        documents = parse_structured_malumot_documents(raw_text)
        seen_keys = {document_lookup_key(document) for document in documents}

        for index, item in enumerate(parse_json_objects_from_text(raw_text), start=1):
            document = normalize_source_item(item, index)
            if document and document_lookup_key(document) not in seen_keys:
                documents.append(document)
                seen_keys.add(document_lookup_key(document))
        if not documents:
            documents = parse_plain_text_documents(raw_text)
    else:
        if isinstance(data, list):
            for index, item in enumerate(data, start=1):
                document = normalize_source_item(item, index)
                if document:
                    documents.append(document)
        elif isinstance(data, dict):
            document = normalize_source_item(data, 1)
            if document:
                documents.append(document)
        else:
            documents = parse_plain_text_documents(raw_text)

    return documents


def delete_all_collections(chroma_client) -> None:
    for collection in chroma_client.list_collections():
        name = collection.name if hasattr(collection, "name") else str(collection)
        try:
            chroma_client.delete_collection(name=name)
            logger.info(f"🗑️ Eski vektor collection o'chirildi: {name}")
        except Exception as exc:
            logger.warning(f"⚠️ '{name}' collectionini o'chirib bo'lmadi: {exc}")


def main():
    logger.info("=== VEKTOR BAZANI TOZALASH VA MALUMOT.TXT DAN QAYTA YARATISH BOSHLANDI ===")

    documents = load_rag_source_documents()
    if not documents:
        logger.error("❌ Vektorlash uchun hujjat topilmadi.")
        return

    db_folder = os.path.join(os.getcwd(), "vektor_baza")
    chroma_client = chromadb.PersistentClient(path=db_folder)
    delete_all_collections(chroma_client)
    collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)

    logger.info(f"'{RAG_SOURCE_FILE}' faylidan {len(documents)} ta hujjat topildi.")
    logger.info("Faqat shu fayldagi ma'lumotlar vektor bazaga yoziladi.")

    for index, document in enumerate(documents, start=1):
        try:
            formatted_text = (
                f"Modda: {document['article']}\n"
                f"Mazmuni: {document['content']}\n"
                f"Manba: {document['source']}"
            )
            response = ollama.embeddings(model=EMBEDDING_MODEL, prompt=formatted_text)
            embedding = response["embedding"]

            collection.add(
                ids=[f"malumot_{index}_{document['id']}"],
                embeddings=[embedding],
                documents=[document["content"]],
                metadatas=[{
                    "source": document["source"],
                    "article": document["article"],
                }],
            )
            logger.info(f"✔ Vektorlandi ({index}/{len(documents)}): {document['article']}")
        except Exception as exc:
            logger.error(f"❌ Hujjatni yuklashda xatolik ({document['article']}): {exc}")

    logger.info("==================================================")
    logger.info("✅ VEKTOR BAZA TOZALANDI VA FAQAT MALUMOT.TXT ASOSIDA QAYTA YARATILDI!")
    logger.info("Endi backend serverni qayta ishga tushiring: python app.py")
    logger.info("==================================================")


if __name__ == "__main__":
    main()
