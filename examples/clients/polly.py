import asyncio
from pathlib import Path

from rboto import PollyClient


async def main() -> None:
    client = PollyClient(region="us-east-1")
    response = await client.synthesize_speech(
        text="Hello from rboto", output_format="mp3", voice_id="Joanna",
    )
    audio = await response.audio_stream.read()
    await asyncio.to_thread(Path("speech.mp3").write_bytes, audio)


if __name__ == "__main__":
    asyncio.run(main())
