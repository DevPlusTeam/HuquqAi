import os
import logging
import sys
import ollama
import chromadb

# LOG TIZIMINI SOZLASH
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("rag_chatbot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Chatbot_Logger")

# 1. TAYYOR VEKTOR BAZAGA ULANISH
db_folder = os.path.join(os.getcwd(), "vektor_baza")
if not os.path.exists(db_folder):
    logger.error("❌ 'vektor_baza' topilmadi! Avval 'python ingest.py' buyrug'ini bajaring.")
    sys.exit()

chroma_client = chromadb.PersistentClient(path=db_folder)
collection = chroma_client.get_collection(name="legal_rag_storage")

def savol_ber(savol):
    try:
        logger.info("🔎 Savol bo'yicha qidiruv boshlandi...")
        savol_embedding = ollama.embeddings(model="nomic-embed-text", prompt=savol)["embedding"]
        
        # Tayyor bazadan eng yaqin natijani qidirish
        results = collection.query(query_embeddings=[savol_embedding], n_results=1)
        
        kontekst = ""
        topilgan_manba = "Noma'lum"
        topilgan_modda = "Noma'lum"
        
        if results['documents'] and len(results['documents'][0]) > 0:
            kontekst = results['documents'][0][0]
            if results['metadatas'] and len(results['metadatas'][0]) > 0:
                topilgan_manba = results['metadatas'][0][0].get('source', 'Mavjud emas')
                topilgan_modda = results['metadatas'][0][0].get('article', 'Mavjud emas')
            
            # --- YANGILANGAN TERMINAL MONITORING BLOKI ---
            print("\n" + "[RAG TIZIMI MONITORINGI]".center(60, "-"))
            print(f"🎯 Topilgan hujjat : {topilgan_modda}")
            print(f"🔗 Rasmiy havola   : {topilgan_manba}")
            print(f"📄 Bazadagi matni  :\n{kontekst}")  # Shu yerda topilgan matn to'liq chiqadi
            print("-" * 60 + "\n")
        else:
            logger.warning("Mos keladigan qonun hujjati topilmadi.")

        # Prompt shabloni
        prompt = f"""Siz O'zbekiston Respublikasi Fuqarolik kodeksi bo'yicha sun'iy intellekt advokatizsiz.
Faqatgina quyida taqdim etilgan Kontekst ma'lumotlaridan foydalanib savolga aniq, tushunarli va to'liq o'zbek tilida javob bering.
O'zingizdan qonun normalarini to'qimang. Javob yakunida manba havolasini ham eslatib o'ting.

Kontekst:
{kontekst}

Savol: {savol}
Javob:"""

        logger.info("🧠 Llama3.2:3b modeliga kontekst uzatildi. Javob kutilmoqda...")
        response = ollama.generate(model="llama3.2:3b", prompt=prompt)
        
        return response['response']
        
    except Exception as e:
        logger.error(f"Chatbot xatoligi: {str(e)}")
        return "Tizimda texnik xatolik yuz berdi."

if __name__ == "__main__":
    print("\n" + "="*60)
    print("⚖️  Fuqarolik Kodeksi AI Chatboti ishga tushdi!")
    print("Dastur tayyor bazadan juda tez ishlaydi. Chiqish uchun 'exit' deb yozing.")
    print("="*60 + "\n")
    
    while True:
        savol = input("📝 Qonun bo'yicha savolingiz: ")
        if savol.lower() == 'exit':
            logger.info("Dastur tugatildi.")
            break
            
        if savol.strip():
            javob = savol_ber(savol)
            print(f"🤖 Advokat-Model javobi:\n{javob}\n")
            print("=" * 60)