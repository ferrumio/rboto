import asyncio
import wave
from collections.abc import AsyncIterator

from rboto import TranscribeStreamingClient
from rboto_transcribe_streaming.types import AudioStream


async def audio_events() -> AsyncIterator[AudioStream]:
    audio = await asyncio.to_thread(wave.open, "speech.wav", "rb")
    try:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("Use mono, 16-bit PCM audio sampled at 16 kHz")
        while chunk := await asyncio.to_thread(audio.readframes, 1600):
            yield {"audio_event": {"audio_chunk": chunk}}
            await asyncio.sleep(0.1)
    finally:
        await asyncio.to_thread(audio.close)


async def main() -> None:
    client = TranscribeStreamingClient(region="us-east-1")
    response = await client.start_stream_transcription(
        language_code="en-US",
        media_sample_rate_hertz=16000,
        media_encoding="pcm",
        audio_stream=audio_events(),
    )
    async for event in response["transcript_result_stream"]:
        print(event)


if __name__ == "__main__":
    asyncio.run(main())
