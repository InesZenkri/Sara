import os
import io
import requests
from typing import List, Optional
from PIL import Image
import dotenv

dotenv.load_dotenv()

class FacebookError(Exception):
    pass


def _get_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise FacebookError(f"Missing required environment variable: {name}")
    return val


def _image_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def post_photos_to_page(images: List[Image.Image], caption: Optional[str] = None) -> int:
    """Post one or more images to a Facebook Page.

    Minimal implementation: uploads each image as its own photo post on the Page.
    This avoids complex multi-attachment publishing. Returns the count of successfully posted images.

    Requires env vars:
      - FACEBOOK_PAGE_ID
      - FACEBOOK_PAGE_ACCESS_TOKEN (a Page Access Token with pages_manage_posts, pages_read_engagement)
    """
    if not images:
        return 0

    page_id = _get_env("FACEBOOK_PAGE_ID")
    token = _get_env("FACEBOOK_PAGE_ACCESS_TOKEN")

    url = f"https://graph.facebook.com/v21.0/{page_id}/photos"

    success = 0
    for idx, img in enumerate(images):
        files = {
            'source': ('image.png', _image_to_bytes(img), 'image/png')
        }
        data = {
            'access_token': token,
        }
        if caption and idx == 0:
            # Set caption on the first image only to avoid repeated captions
            data['caption'] = caption
        try:
            resp = requests.post(url, data=data, files=files, timeout=60)
            if resp.status_code != 200:
                raise FacebookError(f"Facebook API error: {resp.status_code} {resp.text}")
            success += 1
        except Exception as e:
            # Continue trying next images, but keep count
            print(f"Failed to post image {idx+1}: {e}")
            continue

    return success

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Post an image to your Facebook Page via Graph API")
    parser.add_argument("--image_path", help="Path to the image file to upload", default='/Users/zenkri/Desktop/img.png')
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
