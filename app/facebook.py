import os
import io
import requests
from typing import List, Optional
from PIL import Image
# Optional HEIC/HEIF support for Pillow
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    # If pillow-heif isn't installed, Pillow may fail to open .heic/.heif files.
    # The CLI will surface a helpful error message on failure.
    pass
import dotenv

dotenv.load_dotenv()

class FacebookError(Exception):
    pass


def _get_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise FacebookError(f"Missing required environment variable: {name}")
    return val


def _image_to_bytes(img: Image.Image) -> tuple[bytes, str, str]:
    """Convert PIL Image to bytes with appropriate format.

    Returns:
        tuple: (image_bytes, filename, mime_type)
    """
    buf = io.BytesIO()

    # Determine the best format based on image properties
    # Use PNG for images with transparency, JPEG for everything else
    if img.mode in ('RGBA', 'LA', 'P'):
        # Image has or might have transparency
        if img.mode == 'P':
            # Palette mode - check if it has transparency
            if 'transparency' in img.info:
                img = img.convert('RGBA')
                format_type = 'PNG'
            else:
                img = img.convert('RGB')
                format_type = 'JPEG'
        else:
            format_type = 'PNG'
    else:
        # No transparency - use JPEG for better compression
        if img.mode != 'RGB':
            img = img.convert('RGB')
        format_type = 'JPEG'

    # Save with appropriate format
    if format_type == 'JPEG':
        img.save(buf, format='JPEG', quality=95, optimize=True)
        filename = 'image.jpg'
        mime_type = 'image/jpeg'
    else:
        img.save(buf, format='PNG', optimize=True)
        filename = 'image.png'
        mime_type = 'image/png'

    return buf.getvalue(), filename, mime_type


def post_photos_to_page(images: List[Image.Image], caption: Optional[str] = None) -> int:
    """Create a standard Page post (feed post) with one or more images attached.

    Implementation:
    1) Upload each image to /{page-id}/photos with published=false to get media_fbids
    2) Create a single /{page-id}/feed post with attached_media referencing those media_fbids

    Returns the number of images attached to the created post (0 if failed or no images).

    Requires env vars:
      - FACEBOOK_PAGE_ID
      - FACEBOOK_PAGE_ACCESS_TOKEN (Page Access Token with pages_manage_posts)
    """
    if not images:
        return 0

    page_id = _get_env("FACEBOOK_PAGE_ID")
    token = _get_env("FACEBOOK_PAGE_ACCESS_TOKEN")

    # 1) Upload images as unpublished photos to obtain media_fbid IDs
    upload_url = f"https://graph.facebook.com/v21.0/{page_id}/photos"
    media_fbids: List[str] = []

    for img in images:
        img_bytes, filename, mime_type = _image_to_bytes(img)
        files = {
            'source': (filename, img_bytes, mime_type)
        }
        data = {
            'access_token': token,
            'published': 'false',
        }
        try:
            resp = requests.post(upload_url, data=data, files=files, timeout=60)
            if resp.status_code != 200:
                raise FacebookError(f"Facebook photo upload error: {resp.status_code} {resp.text}")
            payload = resp.json()
            media_id = payload.get('id')
            if media_id:
                media_fbids.append(media_id)
        except Exception as e:
            print(f"Failed to upload image: {e}")
            continue

    if not media_fbids:
        return 0

    # 2) Create the feed post with attached_media
    feed_url = f"https://graph.facebook.com/v21.0/{page_id}/feed"
    data = {
        'access_token': token,
    }
    if caption:
        data['message'] = caption

    # attached_media[n] must be JSON strings like {"media_fbid":"<ID>"}
    for idx, media_id in enumerate(media_fbids):
        data[f"attached_media[{idx}]"] = f'{{"media_fbid":"{media_id}"}}'

    try:
        resp = requests.post(feed_url, data=data, timeout=60)
        if resp.status_code != 200:
            raise FacebookError(f"Facebook feed post error: {resp.status_code} {resp.text}")
        # On success, return number of images attached
        return len(media_fbids)
    except Exception as e:
        print(f"Failed to create feed post: {e}")
        return 0

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Create a Facebook Page post (feed) with an image via Graph API")
    parser.add_argument("--image_path", help="Path to the image file to upload (.png, .jpg, .jpeg, .gif, .tif, .tiff, .bmp, .heic, .heif, .webp)", default='/Users/zenkri/Desktop/img.png')
    parser.add_argument("--caption", "-c", help="Optional caption for the post", default='This is my first post')
    args = parser.parse_args()

    # Validate file path
    if not os.path.isfile(args.image_path):
        print(f"File not found: {args.image_path}")
        sys.exit(2)

    # Open image
    try:
        img = Image.open(args.image_path)
    except Exception as e:
        print(f"Failed to open image: {e}")
        sys.exit(2)

    # Post to Facebook
    try:
        count = post_photos_to_page([img], caption=args.caption)
        if count > 0:
            print(f"Successfully posted {count} image(s) to Facebook.")
            sys.exit(0)
        else:
            print("No images were posted.")
            sys.exit(1)
    except FacebookError as fe:
        print(f"Facebook error: {fe}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)
