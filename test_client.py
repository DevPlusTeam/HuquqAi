import asyncio
import io
import json
import os
import tempfile

import sounddevice as sd
import soundfile as sf
import speech_recognition as sr
import websockets


def play_received_audio(audio_bytes):
    try:
        data, sample_rate = decode_audio_bytes(audio_bytes)
        sd.play(data, sample_rate)
        sd.wait()
    except Exception as exc:
        print(f"Ovozni chalishda xato: {exc}")


def decode_audio_bytes(audio_bytes):
    audio_stream = io.BytesIO(audio_bytes)

    try:
        return sf.read(audio_stream, dtype="float32")
    except Exception:
        from pydub import AudioSegment
        import numpy as np

        audio_stream.seek(0)
        audio = AudioSegment.from_file(audio_stream)
        samples = np.array(audio.get_array_of_samples()).astype("float32")
        if audio.channels > 1:
            samples = samples.reshape((-1, audio.channels))

        max_value = float(1 << (8 * audio.sample_width - 1))
        return samples / max_value, audio.frame_rate


def record_question_audio():
    sample_rate = int(os.getenv("ROBOT_MIC_SAMPLE_RATE", "44100"))
    max_seconds = int(os.getenv("ROBOT_MIC_MAX_SECONDS", "60"))

    input("\nYozishni boshlash uchun Enter bosing...")
    print("Gapiring. Yozishni to'xtatish uchun Enter bosing.")
    recording = sd.rec(
        int(max_seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
    )
    input()
    sd.stop()
    return recording, sample_rate


def transcribe_audio(recording, sample_rate):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as audio_file:
        audio_path = audio_file.name

    try:
        sf.write(audio_path, recording, sample_rate)
        recognizer = sr.Recognizer()
        with sr.AudioFile(audio_path) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data, language="uz-UZ")
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


async def receive_robot_answer(ws):
    while True:
        message = await ws.recv()

        if isinstance(message, bytes):
            await asyncio.to_thread(play_received_audio, message)
            continue

        data = json.loads(message)
        status = data.get("status")

        if status == "processing":
            print(data.get("text", "Javob tayyorlanmoqda..."))
        elif status == "ready":
            print(f"Robot: {data.get('text', '')}")
            audio_bytes = await ws.recv()
            if isinstance(audio_bytes, bytes):
                await asyncio.to_thread(play_received_audio, audio_bytes)
            else:
                print(audio_bytes)
            return
        elif status == "error":
            print(f"Server xatosi: {data.get('text', '')}")
            return


async def start_client():
    host = os.getenv("ROBOT_HOST", "127.0.0.1")
    port = os.getenv("ROBOT_PORT", "8000")
    path = os.getenv("ROBOT_PATH", "/robot")
    uri = f"ws://{host}:{port}{path}"

    try:
        async with websockets.connect(uri) as ws:
            print(f"Robot serverga ulandik: {uri}")

            while True:
                recording, sample_rate = record_question_audio()
                try:
                    text = transcribe_audio(recording, sample_rate)
                except Exception as exc:
                    print(f"Ovozni matnga aylantirishda xato: {exc}")
                    continue

                print(f"Siz: {text}")
                await ws.send(text)
                await receive_robot_answer(ws)
    except KeyboardInterrupt:
        print("\nKlient to'xtatildi.")
    except Exception as exc:
        print(f"Ulanish xatosi: {exc}")


if __name__ == "__main__":
    asyncio.run(start_client())
