import base64
import json
import os
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, Optional, Tuple

from PIL import ImageGrab


@dataclass
class ChessAnalysisResult:
    """Structured output returned by the chess analyzer."""

    best_move: Optional[str]
    move_description: Optional[str]
    evaluation: Optional[str]
    explanation: str
    confidence: Optional[float] = None


class ChessAnalysisError(RuntimeError):
    """Raised when chess analysis fails."""


def _parse_region(region_str: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    """Parse a comma-separated bounding box string into a tuple."""
    if not region_str:
        return None

    parts = [p.strip() for p in region_str.split(",") if p.strip()]
    if len(parts) != 4:
        raise ChessAnalysisError(
            "CHESS_CAPTURE_REGION must contain four comma-separated integers: left,top,right,bottom"
        )

    try:
        return tuple(int(float(value)) for value in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise ChessAnalysisError(
            "CHESS_CAPTURE_REGION contains non-numeric values. Use integers such as '100,200,900,1000'."
        ) from exc


def _grab_screenshot(region: Optional[Tuple[int, int, int, int]]) -> bytes:
    """Capture the current screen (optionally cropped to region) and return PNG bytes."""
    try:
        image = ImageGrab.grab(bbox=region)
    except OSError as exc:
        raise ChessAnalysisError(
            "Unable to capture the screen. Ensure the app has screen recording permissions in macOS System Settings."
        ) from exc

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _encode_image(image_bytes: bytes) -> str:
    """Base64-encode PNG bytes for the OpenAI Responses API."""
    return base64.b64encode(image_bytes).decode("utf-8")


def _extract_text_from_response(response) -> str:
    """Attempt to extract the textual answer from an OpenAI responses API payload."""
    text = getattr(response, "output_text", None)
    if text:
        return text

    output = getattr(response, "output", None)
    if output:
        try:
            # Response objects expose a richer structure; fall back to building a string
            fragments = []
            for item in output:
                message = getattr(item, "content", None) or []
                for part in message:
                    part_type = getattr(part, "type", None) or part.get("type")
                    if part_type in {"output_text", "text"}:
                        fragments.append(getattr(part, "text", None) or part.get("text", ""))
            text = "\n".join(fragment for fragment in fragments if fragment)
            if text:
                return text
        except Exception:
            pass

    raise ChessAnalysisError("The chess analysis response did not contain any text output.")


def _parse_analysis_payload(payload: str) -> ChessAnalysisResult:
    """Parse the model output into a structured ChessAnalysisResult."""
    payload = payload.strip()

    def _load_json(text: str) -> Optional[Dict]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    data = _load_json(payload)
    if data is None:
        # Attempt to locate the first JSON object inside the text
        try:
            start = payload.index("{")
            end = payload.rindex("}") + 1
            data = _load_json(payload[start:end])
        except (ValueError, json.JSONDecodeError):
            data = None

    if isinstance(data, dict):
        return ChessAnalysisResult(
            best_move=data.get("best_move"),
            move_description=data.get("move_description") or data.get("best_move_verbose") or data.get("move_details"),
            evaluation=data.get("evaluation"),
            explanation=data.get("explanation") or payload,
            confidence=data.get("confidence"),
        )

    # Fallback: treat plain text as explanation
    return ChessAnalysisResult(
        best_move=None,
        move_description=None,
        evaluation=None,
        explanation=payload,
        confidence=None,
    )


def analyze_chess_position(
    openai_client,
    *,
    user_hint: Optional[str] = None,
) -> ChessAnalysisResult:
    """
    Capture the chessboard currently visible on screen and request the best move from OpenAI.

    Args:
        openai_client: Initialised OpenAI client instance.
        user_hint: Optional extra context from the user (e.g., "playing black").

    Returns:
        ChessAnalysisResult describing the suggested move and reasoning.

    Raises:
        ChessAnalysisError: If capture or analysis fails.
    """
    if openai_client is None:
        raise ChessAnalysisError("OpenAI client is not available. Cannot analyze chess position.")

    region = _parse_region(os.getenv("CHESS_CAPTURE_REGION"))
    screenshot_bytes = _grab_screenshot(region)
    encoded_image = _encode_image(screenshot_bytes)
    image_data_url = f"data:image/png;base64,{encoded_image}"

    model = os.getenv("CHESS_ANALYSIS_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
    max_tokens = int(os.getenv("CHESS_ANALYSIS_MAX_TOKENS", "600"))

    base_instructions = (
        "You are an elite chess coach. Analyze the provided chessboard image, "
        "determine which side the user is playing if possible, and recommend the strongest next move "
        "for the user. If the image is not a chess position, explain what you see instead. "
        "Respond with a compact JSON object containing exactly the keys "
        "`best_move` (string SAN), `move_description` (plain English description such as 'Knight from g1 to f3'), "
        "`evaluation` (string summary of the position such as '+1.2 for White'), "
        "`confidence` (number between 0 and 1 reflecting your certainty), and `explanation` "
        "(one or two sentences describing the reasoning). If you cannot find a legal move, set "
        "`best_move` to null and explain why (still include `move_description` with a short explanation)."
    )

    if user_hint:
        base_instructions += f" Additional user hint: {user_hint.strip()}."

    response = openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": base_instructions},
                    {"type": "input_image", "image_url": image_data_url},
                ],
            }
        ],
        max_output_tokens=max_tokens,
    )

    output_text = _extract_text_from_response(response)
    return _parse_analysis_payload(output_text)
