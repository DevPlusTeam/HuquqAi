import os
import re
import json
import sys
import tempfile
import subprocess
import logging
import concurrent.futures
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import ollama
import chromadb

# LOG TIZIMI
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("RAG_API_Logger")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAG_SOURCE_FILE = "malumot.txt"
COLLECTION_NAME = "legal_rag_storage"

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
        "source": RAG_SOURCE_FILE
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
            "source": RAG_SOURCE_FILE
        })
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
    article = str(document.get("article", "")).lower()
    for apostrophe in ("’", "‘", "ʻ", "ʼ", "`", "´", "ʹ"):
        article = article.replace(apostrophe, "'")
    article = re.sub(r"\s+", " ", article).strip()
    match = re.search(r"\b(\d+)\s*[-–—]?\s*modda\b", article)
    if match:
        return match.group(1)
    return article

def load_rag_source_documents() -> list[dict]:
    source_path = os.path.join(os.getcwd(), RAG_SOURCE_FILE)
    if not os.path.exists(source_path):
        logger.warning(f"⚠️ '{RAG_SOURCE_FILE}' topilmadi. RAG qidiruvi ishlamaydi.")
        return []

    try:
        with open(source_path, "r", encoding="utf-8") as file:
            raw_text = file.read()
    except Exception as e:
        logger.warning(f"⚠️ '{RAG_SOURCE_FILE}' o'qishda xatolik: {str(e)}")
        return []

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

    documents.sort(key=lambda document: int(document_lookup_key(document)) if document_lookup_key(document).isdigit() else 10**9)
    logger.info(f"✅ '{RAG_SOURCE_FILE}' dan {len(documents)} ta hujjat yuklandi.")
    return documents

dataset_documents = load_rag_source_documents()

# 1. VEKTOR BAZAGA ULANISH
db_folder = os.path.join(os.getcwd(), "vektor_baza")
collection = None
if not os.path.exists(db_folder):
    logger.warning("⚠️ 'vektor_baza' topilmadi. RAG javoblari ishlamaydi.")
else:
    try:
        chroma_client = chromadb.PersistentClient(path=db_folder)
        collection = chroma_client.get_collection(name=COLLECTION_NAME)
        logger.info("✅ RAG vektor baza ulandi.")
    except Exception as e:
        logger.warning(f"⚠️ RAG vektor bazaga ulanib bo'lmadi: {str(e)}.")

# FastAPI ilovasini yaratish
app = FastAPI(title="Huquq AI - API Server")

# CORS RUXSATNOMALARI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API so'rov modeli
class ChatRequest(BaseModel):
    message: str

class TextToSpeechRequest(BaseModel):
    text: str

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
MICROSOFT_UZBEK_VOICE = os.getenv("MICROSOFT_UZBEK_VOICE", "uz-UZ-SardorNeural")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
STT_PRIMARY_ENGINE = os.getenv("STT_PRIMARY_ENGINE", "google").lower()
STT_ENGINE_TIMEOUT_SECONDS = int(os.getenv("STT_ENGINE_TIMEOUT_SECONDS", "25"))
_whisper_model = None
RAG_NOT_FOUND_ANSWER = "Berilgan hujjatlarda bu savolga tegishli ma’lumot topilmadi."
ROBOT_NOT_FOUND_ANSWER = "Kechirasiz, taqdim etilgan hujjatlarda bu haqda ma'lumot topilmadi."
ROBOT_RAG_SOURCES = {
    "vector_db",
    "exact_dataset",
    "exact_malumot_txt",
    "keyword_malumot_txt",
}
MAX_RAG_RESULTS = int(os.getenv("MAX_RAG_RESULTS", "1"))
MIN_LEXICAL_SCORE = int(os.getenv("MIN_LEXICAL_SCORE", "6"))
DATASET_ARTICLE_LOOKUP = {}
DATASET_SEARCH_INDEX = []
SEARCH_STOPWORDS = {
    "va", "yoki", "ham", "bu", "shu", "ushbu", "bilan", "uchun", "agar",
    "nima", "nimalar", "qanday", "qaysi", "qachon", "kim", "haqida",
    "bo'yicha", "boyicha", "to'g'risida", "togrisida", "deb", "ekan",
    "kerak", "mumkin", "menga", "ayt", "tushuntir", "izohlab", "ber",
    "qilib", "qilsa", "bo'lsa", "bolsa", "bor", "yo'q", "yoq"
}
UZBEK_SUFFIXES = (
    "larining", "larning", "laridan", "lariga", "larda", "larni",
    "ning", "dan", "ga", "ni", "da", "lar", "lari", "isi", "si"
)

def build_rag_answer(rag_documents):
    if not rag_documents:
        return RAG_NOT_FOUND_ANSWER

    answer_parts = []
    for idx, document in enumerate(rag_documents[:MAX_RAG_RESULTS], start=1):
        if idx > 1:
            answer_parts.append("\n---\n")
        answer_parts.extend([
            f"**Modda:** {document['article']}",
            "",
            document["content"]
        ])
        source_link = format_source_link(document["source"])
        if source_link:
            answer_parts.extend(["", source_link])

    return "\n".join(answer_parts)

def clean_rag_document(document: str, metadata: dict) -> dict:
    article = metadata.get("article", "Mavjud emas")
    source = metadata.get("source", "Mavjud emas")
    content_lines = []
    found_content_label = False

    for line in (document or "").splitlines():
        stripped = line.strip()
        lowered = stripped.lower()

        if lowered.startswith("modda:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                article = value
            continue

        if lowered.startswith("mazmuni:") or lowered.startswith("matn:"):
            found_content_label = True
            value = stripped.split(":", 1)[1].strip()
            if value:
                content_lines.append(value)
            continue

        if lowered.startswith("manba:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                source = value
            continue

        if stripped or found_content_label:
            content_lines.append(line)

    content = "\n".join(content_lines).strip() or (document or "").strip()
    return {
        "content": content,
        "source": source,
        "article": article
    }

def format_source_link(source: str) -> str:
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        return f"[Manba]({source})"
    return ""

def normalize_text(text: str) -> str:
    normalized = str(text or "").lower()
    for apostrophe in ("’", "‘", "ʻ", "ʼ", "`", "´", "ʹ"):
        normalized = normalized.replace(apostrophe, "'")
    return re.sub(r"\s+", " ", normalized).strip()

def stem_search_token(token: str) -> str:
    for suffix in UZBEK_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token

def tokenize_search_text(text: str) -> list[str]:
    tokens = []
    seen = set()
    for raw_token in re.findall(r"[\wʻʼ']+", normalize_text(text), flags=re.UNICODE):
        token = raw_token.strip("_'ʻʼ")
        if len(token) < 3 or token in SEARCH_STOPWORDS:
            continue

        for value in (token, stem_search_token(token)):
            if value and value not in SEARCH_STOPWORDS and value not in seen:
                seen.add(value)
                tokens.append(value)

    return tokens

def build_dataset_search_index() -> None:
    DATASET_ARTICLE_LOOKUP.clear()
    DATASET_SEARCH_INDEX.clear()

    for document in dataset_documents:
        article = document["article"]
        content = document["content"]
        article_norm = normalize_text(article)
        content_norm = normalize_text(content)
        article_tokens = set(tokenize_search_text(article))
        content_tokens = set(tokenize_search_text(content))
        all_tokens = article_tokens | content_tokens

        article_number = None
        article_match = re.match(r"\s*(\d+)\s*[-–—]?\s*modda\b", article_norm)
        if article_match:
            article_number = article_match.group(1)
            DATASET_ARTICLE_LOOKUP[article_number] = document
            DATASET_ARTICLE_LOOKUP[f"{article_number}-modda"] = document

        doc_id = normalize_text(document.get("id", ""))
        if doc_id and (not doc_id.isdigit() or doc_id == article_number):
            DATASET_ARTICLE_LOOKUP[doc_id] = document

        DATASET_SEARCH_INDEX.append({
            "document": document,
            "article_norm": article_norm,
            "content_norm": content_norm,
            "article_tokens": article_tokens,
            "content_tokens": content_tokens,
            "all_tokens": all_tokens
        })

    logger.info(f"✅ Tezkor JSON qidiruv indeksi tayyor: {len(DATASET_SEARCH_INDEX)} ta modda.")

def score_dataset_document(query_norm: str, query_tokens: list[str], item: dict) -> int:
    score = 0
    if len(query_norm) >= 4:
        if query_norm in item["article_norm"]:
            score += 40
        if query_norm in item["content_norm"]:
            score += 25

    for token in query_tokens:
        if token in item["article_tokens"]:
            score += 8
        if token in item["content_tokens"]:
            score += 3

    return score

def find_dataset_articles_by_keywords(question: str, limit: int = MAX_RAG_RESULTS) -> list[dict]:
    query_norm = normalize_text(question)
    query_tokens = tokenize_search_text(question)
    if len(query_norm) < 4 and not query_tokens:
        return []

    if 1 <= len(query_tokens) <= 2:
        query_phrase = " ".join(query_tokens)
        phrase_matches = [
            item["document"]
            for item in DATASET_SEARCH_INDEX
            if query_phrase in item["article_norm"] or query_phrase in item["content_norm"]
        ]
        if phrase_matches:
            return phrase_matches[:limit]

    scored_documents = []
    for item in DATASET_SEARCH_INDEX:
        score = score_dataset_document(query_norm, query_tokens, item)
        if score >= MIN_LEXICAL_SCORE:
            scored_documents.append((score, item["document"]))

    if not scored_documents:
        return []

    scored_documents.sort(key=lambda result: result[0], reverse=True)
    best_score = scored_documents[0][0]
    cutoff = max(MIN_LEXICAL_SCORE, int(best_score * 0.30))
    return [
        document
        for score, document in scored_documents
        if score >= cutoff
    ][:limit]

def build_rag_context(rag_documents: list[dict]) -> str:
    return "\n\n".join(
        f"[RAG {idx}]\nModda: {doc['article']}\nManba: {doc['source']}\nMatn:\n{doc['content']}"
        for idx, doc in enumerate(rag_documents, start=1)
    )

def extract_article_number(question: str) -> str | None:
    text = normalize_text(question)
    patterns = [
        r"\b(\d+)\s*[-–—]?\s*modda\b",
        r"\bmodda\s*[:#-]?\s*(\d+)\b"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    return None

def find_exact_dataset_article(question: str) -> dict | None:
    article_number = extract_article_number(question)
    if not article_number:
        return None

    return DATASET_ARTICLE_LOOKUP.get(article_number) or DATASET_ARTICLE_LOOKUP.get(f"{article_number}-modda")

build_dataset_search_index()

def is_ai_chat_question(question: str) -> bool:
    text = normalize_text(question)
    compact = re.sub(r"[^\w\s']", " ", text)
    compact = re.sub(r"\s+", " ", compact).strip()

    greeting_phrases = {
        "salom",
        "assalomu alaykum",
        "assalomu aleykum",
        "assalom alaykum",
        "assalom aleykum",
        "hello",
        "hi"
    }
    return compact in greeting_phrases

def get_ai_question_kind(question: str) -> str:
    text = normalize_text(question)
    if text in {"salom", "assalomu alaykum", "assalom alaykum", "hello", "hi"}:
        return "salomlashish"
    if text in {"assalomu aleykum", "assalom aleykum"}:
        return "salomlashish"
    return "oddiy suhbat"

def generate_ai_answer(question: str) -> str:
    question_kind = get_ai_question_kind(question)
    system_prompt = (
        "Siz Huquq AI nomli o'zbekcha yuridik yordamchi chatbotsiz. "
        "Faqat yakuniy javobni yozing. Yorliq, izoh, reja yoki tizim ko'rsatmalarini yozmang. "
        "Javob o'zbekcha lotin yozuvida, qisqa va ravon bo'lsin."
    )
    if question_kind == "salomlashish":
        user_prompt = (
            "Foydalanuvchi salomlashdi. Siz ham salomlashing, o'zingizni Huquq AI deb tanishtiring "
            "va huquqiy savolini yozishini so'rang. 2 gapdan oshirmang."
        )
    else:
        user_prompt = question
    response = ollama.chat(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        options={
            "temperature": 0.2,
            "num_predict": 70,
            "repeat_penalty": 1.25
        }
    )
    return sanitize_model_answer(response.get("message", {}).get("content", "").strip())

def generate_rag_answer(question: str, rag_documents: list[dict]) -> str:
    if not rag_documents:
        return build_rag_answer(rag_documents)

    primary = rag_documents[0]
    system_prompt = """Siz Fuqarolik kodeksi asosida ishlaydigan RAG asosidagi yordamchisiz.

Sizning vazifangiz — foydalanuvchi savoliga faqat berilgan hujjatlar (context) asosida javob berish.

MUHIM QOIDALAR:
1. Faqat berilgan contextdan foydalaning.
2. Agar context savolga tegishli bo‘lmasa, quyidagicha javob bering:
"Berilgan hujjatlarda bu savolga tegishli ma’lumot topilmadi."
3. O‘zingizdan qo‘shimcha ma’lumot qo‘shmang.
4. Boshqa moddalardan taxmin qilib javob bermang.

MUHIM MUAMMO (RAG xatosi):
Ba’zan retrieval noto‘g‘ri modda olib kelishi mumkin.
Bunday contextlar mutlaqo befoyda va ignore qilinishi kerak.

FAKAT QUYIDAGI HOLATLARDA JAVOB BERING:
- Context savolga bevosita tegishli bo‘lsa
- Unda shartnoma, majburiyat, to‘lov, zarar, sotish kabi mavzular bo‘lsa

Aks holda javob bermang.

JAVOB FORMATI:
- Qisqa va tushunarli
- Faqat context asosida
- Hech qanday qo‘shimcha fikr yoki taxmin yo‘q

OUTPUT:
Faqat javob yoki:
"Berilgan hujjatlarda bu savolga tegishli ma’lumot topilmadi.\""""
    user_prompt = f"""Savol: {question}

Context:
Modda: {primary['article']}
{primary['content']}"""
    response = ollama.chat(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        options={
            "temperature": 0,
            "num_predict": 220,
            "repeat_penalty": 1.2
        }
    )
    answer = sanitize_model_answer(response.get("message", {}).get("content", "").strip())
    return answer or RAG_NOT_FOUND_ANSWER

def ensure_article_heading(answer: str, article: str) -> str:
    if not article or article == "Mavjud emas":
        return answer
    if article.lower() in answer.lower():
        return answer
    return f"**Modda:** {article}\n\n{answer}"

def sanitize_model_answer(answer: str) -> str:
    cleaned = re.sub(r"\[Manba\]\(https?://[^\s)]+\)", "", answer)
    cleaned = re.sub(r"\*\*Manba:\*\*\s*https?://\S+", "", cleaned)
    cleaned = re.sub(r"Manba:\s*https?://\S+", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned

UZBEK_NUMBER_UNITS = [
    "nol", "bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti", "sakkiz", "to'qqiz"
]
UZBEK_NUMBER_TENS = {
    10: "o'n",
    20: "yigirma",
    30: "o'ttiz",
    40: "qirq",
    50: "ellik",
    60: "oltmish",
    70: "yetmish",
    80: "sakson",
    90: "to'qson",
}
UZBEK_ORDINAL_WORDS = {
    "nol": "nolinchi",
    "bir": "birinchi",
    "ikki": "ikkinchi",
    "uch": "uchinchi",
    "to'rt": "to'rtinchi",
    "besh": "beshinchi",
    "olti": "oltinchi",
    "yetti": "yettinchi",
    "sakkiz": "sakkizinchi",
    "to'qqiz": "to'qqizinchi",
    "o'n": "o'ninchi",
    "yigirma": "yigirmanchi",
    "o'ttiz": "o'ttizinchi",
    "qirq": "qirqinchi",
    "ellik": "elliginchi",
    "oltmish": "oltmishinchi",
    "yetmish": "yetmishinchi",
    "sakson": "saksoninchi",
    "to'qson": "to'qsoninchi",
    "yuz": "yuzinchi",
    "ming": "minginchi",
    "million": "millioninchi",
    "milliard": "milliardinchi",
}

def number_to_uzbek_words(number: int) -> str:
    if number < 0:
        return f"minus {number_to_uzbek_words(abs(number))}"
    if number < 10:
        return UZBEK_NUMBER_UNITS[number]
    if number < 100:
        tens = (number // 10) * 10
        remainder = number % 10
        words = [UZBEK_NUMBER_TENS[tens]]
        if remainder:
            words.append(UZBEK_NUMBER_UNITS[remainder])
        return " ".join(words)
    if number < 1000:
        hundreds = number // 100
        remainder = number % 100
        words = [UZBEK_NUMBER_UNITS[hundreds], "yuz"]
        if remainder:
            words.append(number_to_uzbek_words(remainder))
        return " ".join(words)

    for scale, scale_name in (
        (1_000_000_000, "milliard"),
        (1_000_000, "million"),
        (1000, "ming"),
    ):
        if number >= scale:
            major = number // scale
            remainder = number % scale
            words = [number_to_uzbek_words(major), scale_name]
            if remainder:
                words.append(number_to_uzbek_words(remainder))
            return " ".join(words)

    return str(number)

def number_to_uzbek_ordinal(number: int) -> str:
    words = number_to_uzbek_words(number).split()
    if not words:
        return ""
    words[-1] = UZBEK_ORDINAL_WORDS.get(words[-1], f"{words[-1]}inchi")
    return " ".join(words)

def replace_numbers_for_tts(text: str) -> str:
    def percent_replacer(match):
        return f"{number_to_uzbek_words(int(match.group(1)))} foiz"

    def year_replacer(match):
        number = int(match.group(1))
        suffix = match.group(2).lower()
        return f"{number_to_uzbek_ordinal(number)} {suffix}"

    def ordinal_noun_replacer(match):
        number = int(match.group(1))
        noun = match.group(2).lower()
        return f"{number_to_uzbek_ordinal(number)} {noun}"

    def integer_replacer(match):
        return number_to_uzbek_words(int(match.group(0)))

    text = re.sub(r"\b(\d+)\s*%", percent_replacer, text)
    text = re.sub(r"\b(\d+)\s+foiz\b", percent_replacer, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s*[-–—]?\s*(yilda|yili|yil)\b", year_replacer, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)\s*[-–—]?\s*(modda|band|qism)\b", ordinal_noun_replacer, text, flags=re.IGNORECASE)
    return re.sub(r"\b\d+\b", integer_replacer, text)

def limit_tts_sentences(text: str, max_sentences: int | None = None) -> str:
    if not max_sentences:
        return text

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) <= max_sentences:
        return text
    return " ".join(sentences[:max_sentences])

def sanitize_tts_plain_text(text: str, max_chars: int = 4500, max_sentences: int | None = None) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("ʼ", "'")
    cleaned = re.sub(r"```[\s\S]*?```", " ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\[\[SOURCE_LINK:[^\]]+\]\]", " ", cleaned)
    cleaned = replace_numbers_for_tts(cleaned)
    cleaned = re.sub(r"[*_#`>{}\[\]()@#$%^&=+|\\/~]+", " ", cleaned)
    cleaned = re.sub(r"[-–—]+", " ", cleaned)
    cleaned = re.sub(r"[;:]+", ". ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = limit_tts_sentences(cleaned, max_sentences=max_sentences)
    return cleaned[:max_chars].strip()

def clean_tts_text(text: str) -> str:
    return sanitize_tts_plain_text(text, max_chars=4500)

def build_robot_context_from_payload(chat_payload: dict) -> str:
    if chat_payload.get("answer_source") not in ROBOT_RAG_SOURCES:
        return ""

    rag_info = chat_payload.get("rag_info") or {}
    context = str(rag_info.get("content") or "").strip()
    if context and ROBOT_NOT_FOUND_ANSWER.lower() not in context.lower():
        return context
    return ""

def generate_robot_context_answer(question: str, context: str) -> str:
    if not context:
        return ROBOT_NOT_FOUND_ANSWER

    system_prompt = f"""Siz ovozli boshqaruvga ega va Vector DB hujjatlari bilan ishlaydigan aqlli robot asistentsiz.
Faqat taqdim etilgan kontekstdan foydalaning.
Agar kontekstda savolga javob bo'lmasa, aynan shunday javob bering: {ROBOT_NOT_FOUND_ANSWER}
Javob faqat TTS uchun tekis matn bo'lsin.
Markdown, kod, JSON, qavslar, maxsus belgilar va ro'yxatlar ishlatmang.
Javob ikki yoki uchta qisqa gapdan oshmasin.
Raqamlar, yillar va foizlarni so'z bilan yozing.
Ohang do'stona, samimiy va professional bo'lsin."""
    user_prompt = f"""Savol:
{question}

Kontekst:
{context}

Javob:"""
    response = ollama.chat(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        options={
            "temperature": 0,
            "num_predict": 120,
            "repeat_penalty": 1.2
        }
    )
    answer = response.get("message", {}).get("content", "").strip()
    return sanitize_tts_plain_text(answer or ROBOT_NOT_FOUND_ANSWER, max_chars=900, max_sentences=3)

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper_model

def transcribe_with_whisper(audio_path: str) -> str:
    model = get_whisper_model()
    segments, _ = model.transcribe(audio_path, language="uz", beam_size=1)
    return " ".join(segment.text.strip() for segment in segments).strip()

def convert_audio_to_wav(audio_path: str) -> str:
    wav_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    wav_path = wav_file.name
    wav_file.close()

    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(wav_path, format="wav")
        return wav_path
    except Exception:
        pass

    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return wav_path

def transcribe_with_speech_recognition(audio_path: str) -> str:
    import speech_recognition as sr
    wav_path = convert_audio_to_wav(audio_path)
    try:
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data, language="uz-UZ").strip()
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

def run_stt_with_timeout(engine_name: str, transcriber, audio_path: str) -> str:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(transcriber, audio_path)
    try:
        return future.result(timeout=STT_ENGINE_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise TimeoutError(
            f"{engine_name} {STT_ENGINE_TIMEOUT_SECONDS} soniyada javob qaytarmadi"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

def transcribe_audio(audio_path: str) -> tuple[str, str]:
    errors = []
    engines = [
        ("speech_recognition", transcribe_with_speech_recognition, "speech_recognition:google:uz-UZ"),
        ("faster-whisper", transcribe_with_whisper, f"faster-whisper:{WHISPER_MODEL}")
    ]

    if STT_PRIMARY_ENGINE in {"whisper", "faster-whisper", "faster_whisper"}:
        engines.reverse()

    for engine_name, transcriber, engine_label in engines:
        try:
            logger.info(f"🎙 STT boshlandi: {engine_label}")
            text = run_stt_with_timeout(engine_name, transcriber, audio_path)
            if text:
                logger.info(f"✅ STT tayyor: {engine_label}")
                return text, engine_label
            errors.append(f"{engine_name}: bo'sh matn qaytdi")
        except Exception as e:
            logger.warning(f"⚠️ STT xatosi ({engine_name}): {str(e)}")
            errors.append(f"{engine_name}: {str(e)}")

    raise HTTPException(
        status_code=500,
        detail=(
            "Ovozni matnga aylantirib bo'lmadi. `faster-whisper` yoki "
            "`SpeechRecognition` + `pydub/ffmpeg` sozlamalarini tekshiring. "
            f"Xatolar: {' | '.join(errors)}"
        )
    )

def get_upload_suffix(file: UploadFile) -> str:
    filename = file.filename or ""
    _, ext = os.path.splitext(filename)
    if ext:
        return ext
    if file.content_type == "audio/webm":
        return ".webm"
    if file.content_type == "audio/wav":
        return ".wav"
    if file.content_type == "audio/mpeg":
        return ".mp3"
    return ".webm"

@app.post("/api/speech-to-text")
async def speech_to_text_endpoint(audio: UploadFile = File(...)):
    suffix = get_upload_suffix(audio)
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path = temp_file.name

    try:
        content = await audio.read()
        if not content:
            raise HTTPException(status_code=400, detail="Audio fayl bo'sh.")

        temp_file.write(content)
        temp_file.close()

        text, engine = transcribe_audio(temp_path)
        return {
            "status": "success",
            "text": text,
            "engine": engine,
            "language": "uz"
        }
    finally:
        if not temp_file.closed:
            temp_file.close()
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/api/text-to-speech")
async def text_to_speech_endpoint(request: TextToSpeechRequest):
    text = clean_tts_text(request.text)
    if not text:
        raise HTTPException(status_code=400, detail="Ovozga aylantirish uchun matn bo'sh.")

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    temp_path = temp_file.name
    temp_file.close()

    try:
        try:
            import edge_tts
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="Microsoft Sardor ovozi uchun `edge-tts` kutubxonasi o'rnatilmagan."
            )

        communicate = edge_tts.Communicate(
            text=text,
            voice=MICROSOFT_UZBEK_VOICE,
            rate="+0%",
            pitch="+0Hz"
        )
        await communicate.save(temp_path)

        with open(temp_path, "rb") as f:
            audio_bytes = f.read()

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": 'inline; filename="sardor-answer.mp3"',
                "X-TTS-Voice": MICROSOFT_UZBEK_VOICE
            }
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    savol = request.message.strip()
    if not savol:
        raise HTTPException(status_code=400, detail="Savol bo'sh bo'lishi mumkin emas")
    
    try:
        if is_ai_chat_question(savol):
            answer = generate_ai_answer(savol)
            return {
                "status": "success",
                "answer_source": "llama_ai",
                "answer": answer,
                "rag_info": {
                    "article": "",
                    "source": "",
                    "content": ""
                }
            }

        rag_documents = []
        kontekst = ""
        topilgan_manba = "Noma'lum"
        topilgan_modda = "Noma'lum"

        exact_article = find_exact_dataset_article(savol)
        if exact_article:
            rag_documents = [exact_article]
            kontekst = build_rag_context(rag_documents)
            return {
                "status": "success",
                "answer_source": "exact_malumot_txt",
                "model": RAG_SOURCE_FILE,
                "answer": build_rag_answer(rag_documents),
                "rag_info": {
                    "article": exact_article["article"],
                    "source": exact_article["source"],
                    "content": kontekst
                }
            }

        keyword_articles = find_dataset_articles_by_keywords(savol)
        if keyword_articles:
            rag_documents = keyword_articles
            kontekst = build_rag_context(rag_documents)
            topilgan_modda = "; ".join(doc["article"] for doc in rag_documents)
            topilgan_manba = "; ".join(doc["source"] for doc in rag_documents)
            return {
                "status": "success",
                "answer_source": "keyword_malumot_txt",
                "model": RAG_SOURCE_FILE,
                "answer": build_rag_answer(rag_documents),
                "rag_info": {
                    "article": topilgan_modda,
                    "source": topilgan_manba,
                    "content": kontekst
                }
            }

        if collection is None:
            raise HTTPException(
                status_code=503,
                detail="RAG vektor bazaga ulanmagan. Avval `python ingest.py` ni ishga tushiring."
            )

        # JSON qidiruv yetarli natija bermasa, sekinroq semantik vector DB fallback ishlaydi.
        savol_embedding = ollama.embeddings(model=EMBEDDING_MODEL, prompt=savol)["embedding"]
        results = collection.query(query_embeddings=[savol_embedding], n_results=MAX_RAG_RESULTS)

        documents = results.get("documents") or [[]]
        metadatas = results.get("metadatas") or [[]]

        for idx, document in enumerate(documents[0]):
            metadata = metadatas[0][idx] if metadatas and metadatas[0] and idx < len(metadatas[0]) else {}
            rag_documents.append(clean_rag_document(document, metadata))

        if rag_documents:
            kontekst = build_rag_context(rag_documents)
            topilgan_modda = "; ".join(doc["article"] for doc in rag_documents)
            topilgan_manba = "; ".join(doc["source"] for doc in rag_documents)
            
            # --- TERMINAL MONITORING ---
            print("\n" + "[RAG TIZIMI MONITORINGI]".center(60, "-"))
            for idx, doc in enumerate(rag_documents, start=1):
                print(f"🎯 RAG {idx} hujjat : {doc['article']}")
                print(f"🔗 RAG {idx} havola : {doc['source']}")
                print(f"📄 RAG {idx} matni  :\n{doc['content']}\n")
            print("-" * 60 + "\n")
        else:
            logger.warning("Mos keladigan RAG hujjati topilmadi.")

        answer = build_rag_answer(rag_documents)
        
        return {
            "status": "success",
            "answer_source": "vector_db",
            "model": "direct_context",
            "answer": answer,
            "rag_info": {
                "article": topilgan_modda,
                "source": topilgan_manba,
                "content": kontekst
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Xatolik: {str(e)}")
        raise HTTPException(status_code=500, detail="Tizimda ichki texnik xatolik")

@app.websocket("/robot")
async def robot_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    while True:
        try:
            user_text = (await websocket.receive_text()).strip()
            if not user_text:
                await websocket.send_text(json.dumps({
                    "status": "error",
                    "text": "Savol bo'sh bo'lishi mumkin emas."
                }, ensure_ascii=False))
                continue

            await websocket.send_text(json.dumps({
                "status": "processing",
                "text": "Javob tayyorlanmoqda..."
            }, ensure_ascii=False))

            if is_ai_chat_question(user_text):
                chat_payload = {"answer_source": "no_context", "rag_info": {}}
                response_text = ROBOT_NOT_FOUND_ANSWER
            else:
                chat_payload = await chat_endpoint(ChatRequest(message=user_text))
                robot_context = build_robot_context_from_payload(chat_payload)
                response_text = generate_robot_context_answer(user_text, robot_context)
            if not response_text:
                raise HTTPException(status_code=500, detail="AI javobi bo'sh qaytdi.")

            audio_response = await text_to_speech_endpoint(TextToSpeechRequest(text=response_text))

            await websocket.send_text(json.dumps({
                "status": "ready",
                "text": response_text,
                "answer_source": chat_payload.get("answer_source", ""),
                "rag_info": chat_payload.get("rag_info", {})
            }, ensure_ascii=False))
            await websocket.send_bytes(audio_response.body)

        except WebSocketDisconnect:
            break
        except HTTPException as exc:
            await websocket.send_text(json.dumps({
                "status": "error",
                "text": exc.detail
            }, ensure_ascii=False))
        except Exception as exc:
            logger.error(f"Robot WebSocket xatoligi: {str(exc)}")
            try:
                await websocket.send_text(json.dumps({
                    "status": "error",
                    "text": f"Server ichki xatosi: {exc}"
                }, ensure_ascii=False))
            except Exception:
                break

# Static fayllarni xavfsiz ulash
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
