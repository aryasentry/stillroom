"""Small local image derivatives, with no remote fetches or original mutation."""
import io
from PIL import Image,ImageOps
from .formats import InvalidAsset

THUMBNAIL_EDGE=640


def image_thumbnail(data):
    """Called only after the source passes bounded image validation."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            # Shrink before the EXIF orientation copy to bound the derived image.
            source.thumbnail((THUMBNAIL_EDGE,THUMBNAIL_EDGE),Image.Resampling.LANCZOS)
            oriented=ImageOps.exif_transpose(source)
            transparent='A' in oriented.getbands() or 'transparency' in oriented.info
            thumbnail=oriented.convert('RGBA' if transparent else 'RGB')
            # Derivatives contain pixels only, never source EXIF/ICC metadata.
            thumbnail.info.clear()
            output=io.BytesIO()
            if transparent:
                thumbnail.save(output,format='PNG',optimize=True)
                suffix='.png';mime='image/png';format='PNG'
            else:
                thumbnail.save(output,format='JPEG',quality=82,optimize=True)
                suffix='.jpg';mime='image/jpeg';format='JPEG'
            return output.getvalue(),mime,suffix,{'width':thumbnail.width,'height':thumbnail.height,'format':format}
    except Exception as exc:
        raise InvalidAsset('Image pixels could not be decoded to create a thumbnail') from exc
