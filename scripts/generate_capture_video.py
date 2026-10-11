"""Encode the CC0 synthetic camera sequence as H.264; no geometry enters decoding."""
from pathlib import Path
import av
from PIL import Image

root=Path(__file__).resolve().parent.parent
photos=sorted((root/'fixtures/capture-room/images').glob('*.jpg'))
assert len(photos)==54
path=root/'fixtures/capture-room-video.mp4'
with av.open(str(path),'w') as container:
    stream=container.add_stream('libx264',rate=12)
    stream.width=960;stream.height=720;stream.pix_fmt='yuv420p';stream.options={'crf':'18'}
    for photo in photos:
        with Image.open(photo) as im:
            for _ in range(6):
                for packet in stream.encode(av.VideoFrame.from_image(im.convert('RGB'))):container.mux(packet)
    for packet in stream.encode():container.mux(packet)
print(path.name,path.stat().st_size)
