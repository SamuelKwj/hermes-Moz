import asyncio
from edge_tts import list_voices

async def main():
    voices = await list_voices()
    zh = [v for v in voices if "zh-CN" in v["Locale"] and "Neural" in v["ShortName"]]
    for v in zh[:10]:
        print(f"{v['ShortName']:45s} {v['FriendlyName']}")

asyncio.run(main())
