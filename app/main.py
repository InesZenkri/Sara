# In main, start a hotword listener. if it hears sara, call a function that prints "Heard sara"
from enum import Enum
from audio import HotwordListener
from clipboard import ClipboardMonitor
from typing import Optional
import time
import os
import instructor
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator
import subprocess
from pymacnotifier import MacNotifier
from bg_remove import remove_background
from dotenv import load_dotenv
from tts import CartesiaTTS
from llm_config import llm_settings, instructor_mode_name
from tts_local import make_tts
from facebook import post_photos_to_page
from chess_analyzer import analyze_chess_position, ChessAnalysisError

# Load environment from .env so API keys and config are available
load_dotenv()

# Memory functionality flag - disabled by default
ENABLE_MEMORY = os.getenv("ENABLE_MEMORY", "false").lower() == "true"

# Conditionally import memory modules only if enabled
if ENABLE_MEMORY:
    from chroma_memory import get_memory, search_memory_tool
    print("Memory functionality enabled")
else:
    print("Memory functionality disabled")

def create_client():
    """Create and return the LLM client configuration (OpenAI or any OpenAI-compatible provider).

    Settings come from llm_config: SARA_LLM_* or OPENAI_* environment variables.
    """
    settings = llm_settings()
    api_key = settings["api_key"]
    if not api_key:
        raise RuntimeError("No LLM API key set. Set OPENAI_API_KEY (or SARA_LLM_API_KEY) in your .env.")

    model_name = settings["model"]
    try:
        openai_client = OpenAI(api_key=api_key, base_url=settings["base_url"])
        mode_name = instructor_mode_name()
        if mode_name:
            instructor_client = instructor.from_openai(openai_client, mode=getattr(instructor.Mode, mode_name))
        else:
            instructor_client = instructor.from_openai(openai_client)
        return {
            "instructor": instructor_client,
            "native": openai_client,
            "model": model_name,
            "provider": "openai",
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize LLM client: {exc}") from exc

client_config = create_client()
print(f"Using LLM model: {client_config['model']}")

monitor = ClipboardMonitor()
monitor.start(log=True)

# Initialize the Mac notifier with a default title
notifier = MacNotifier(default_title="Sara")

# Initialize TTS client: Cartesia if a key is set, otherwise the macOS `say` voice (see tts_local.py)
tts_client = make_tts()


class CopyTextToClipboardResponse(BaseModel):
    """Structured response containing the content to be copied to clipboard."""

    content_for_clipboard: str


class ActionType(Enum):
    """Types of actions the assistant can take."""

    COPY_TEXT_TO_CLIPBOARD = "COPY_TEXT_TO_CLIPBOARD"
    SHORT_REPLY = "SHORT_REPLY"
    NO_ACTION = "NO_ACTION"
    MAKE_MEME = "MAKE_MEME"
    REMOVE_BACKGROUND = "REMOVE_BACKGROUND"
    SEARCH_MEMORY = "SEARCH_MEMORY"
    SAVE_TO_MEMORY = "SAVE_TO_MEMORY"
    POST_TO_FACEBOOK = "POST_TO_FACEBOOK"
    ANALYZE_CHESS = "ANALYZE_CHESS"


class BaseAssistantResponse(BaseModel):
    """Base assistant response for common fields."""
    thinking: str
    actionType: ActionType
    message: str = Field(
        ...,
        description="A short message to the user about what action was taken. Be extremely concise, since there is a 50 character limit.",
    )
    emoji: Optional[str] = Field(
        None,
        description="Optional icon label for the notification. Use 'standard' for the Sara icon or leave empty to use the default.",
    )
    content_for_clipboard: Optional[str] = None
    meme_top_text: Optional[str] = Field(None, description="Top text for the meme")
    meme_bottom_text: Optional[str] = Field(None, description="Bottom text for the meme")
    facebook_caption: Optional[str] = Field(None, description="Caption to include when posting images to Facebook")
    
    @field_validator('actionType', mode='before')
    @classmethod
    def validate_action_type(cls, v):
        if isinstance(v, str):
            # Try to convert string to enum
            for action in ActionType:
                if action.value == v:
                    return action
            # If no match found, raise error with helpful message
            valid_values = [action.value for action in ActionType]
            raise ValueError(f"Invalid actionType: {v}. Must be one of: {valid_values}")
        return v

class AssistantResponseWithMemory(BaseAssistantResponse):
    """Assistant response with memory capabilities."""
    memory_search_query: Optional[str] = Field(
        None, description="Query to search memory when action is SEARCH_MEMORY"
    )
    memory_save_content: Optional[str] = Field(
        None, description="Content to save to memory when action is SAVE_TO_MEMORY"
    )
    memory_save_type: Optional[str] = Field(
        None, description="Type of memory entry to save (e.g., 'important_info', 'preference', 'note', 'reminder')"
    )

# Use the appropriate response model based on memory setting
AssistantResponse = AssistantResponseWithMemory if ENABLE_MEMORY else BaseAssistantResponse


hotword = "sara"


def speak_response(text: str):
    """Generate and play voice response using Cartesia TTS."""
    if not tts_client or not text:
        return
    
    try:
        # Generate speech
        audio_data = tts_client.synthesize(text)
        
        # Play audio using system audio player
        import tempfile
        import subprocess
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
            tmp_file.write(audio_data)
            tmp_file.flush()
            
            # Play audio file
            subprocess.run(['afplay', tmp_file.name], check=True)
            
            # Clean up
            os.unlink(tmp_file.name)
            
    except Exception as e:
        print(f"Error playing voice response: {e}")

def notify_user(message: Optional[str], emotion: Optional[str] = None):
    """Show a concise notification while speaking full response."""
    if not message:
        return

    notification_text = message if len(message) <= 50 else f"{message[:47]}..."
    notifier.simple_notify(message=notification_text, emotion=emotion)
    speak_response(message)


def on_hotword_detected(text, audio):
    if ENABLE_MEMORY:
        # Full memory-enabled version
        return on_hotword_detected_with_memory(text, audio)
    else:
        # Simplified version without memory
        return on_hotword_detected_simple(text, audio)

def on_hotword_detected_simple(text, audio):
    """Simplified version without memory functionality."""
    # Get current clipboard content
    clipboard_data = monitor.get_last()
    if clipboard_data is None:
        clipboard_str = "Empty"
    else:
        clipboard_data_type, clipboard_content = clipboard_data
        clipboard_str = f"Type: {clipboard_data_type}, Content: {str(clipboard_content)[:200]}..." if clipboard_content else "Empty"
    
    # Build simple prompt
    prompt = f"Voice Command: {text}"
    prompt += "\n\n"
    prompt += "Clipboard Content:\n"
    prompt += clipboard_str

    try:
        chat_completion = client_config["instructor"].chat.completions.create(
            model=client_config["model"],
            response_model=AssistantResponse,
            messages=[
                {
                    "role": "system",
                    "content": f"""You are {hotword}, a voice assistant that responds to commands. You help manage clipboard content.

ACTIONS AVAILABLE:
- COPY_TEXT_TO_CLIPBOARD: Copy specific text to clipboard (most common action)
- MAKE_MEME: Create meme from clipboard image with top/bottom text  
- REMOVE_BACKGROUND: Remove background from clipboard image, making it transparent
- POST_TO_FACEBOOK: Post images from clipboard to your Facebook Page. Analyze the image to generate a caption that describes the image and will be included in the post.
- ANALYZE_CHESS: Capture the current screen and analyze the visible chess position to recommend the strongest move.
- SHORT_REPLY: Just notify user with a message
- NO_ACTION: When no action is needed

INSTRUCTIONS:
- Only choose ANALYZE_CHESS when the user clearly references chess or wants help finding their next move.
- Focus on helping with clipboard content
- If user asks to convert data formats, do it in full without truncating
- Provide medium-length (2-3 sentence) explanations when describing code or technical behavior, even if they exceed 50 characters. The app will shorten notification banners automatically.
- For quick confirmations keep the core notification concise.
- For image captioning for Facebook posts. Generate engaging captions when the you want to sell an item. If the price is not provided, search the internet for typical pricing.
"""
                },
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as e:
        print(f"Error during response generation: {e}")
        # Create a fallback response
        chat_completion = AssistantResponse(
            thinking="Error occurred during response generation",
            actionType=ActionType.SHORT_REPLY,
            message="Sorry, I encountered an error processing your request."
        )

    print(f"Response: {chat_completion}")

    # Handle the different action types
    if chat_completion.actionType == ActionType.NO_ACTION:
        if chat_completion.message:
            notify_user(chat_completion.message)
        print("No action needed.")
        return

    if (chat_completion.actionType == ActionType.COPY_TEXT_TO_CLIPBOARD 
        and chat_completion.content_for_clipboard):
        monitor.copy_text(chat_completion.content_for_clipboard)
        print("Clipboard updated.")
        notify_user(chat_completion.message)
        return

    if chat_completion.actionType == ActionType.MAKE_MEME:
        # Handle meme creation
        print("Creating meme...")
        clipboard_data = monitor.get_last()
        if clipboard_data is None:
            print("No clipboard data found to create a meme.")
            return
        data_type, value = clipboard_data
        if data_type is None or value is None or data_type != "image":
            print("No image found in clipboard to create a meme.")
            return
        from meme import make_meme

        meme_image = make_meme(
            value,
            upper_text=chat_completion.meme_top_text or "",
            lower_text=chat_completion.meme_bottom_text or "",
        )
        monitor.copy_image(meme_image)
        print("Meme created and copied to clipboard.")
        notify_user(chat_completion.message)
        return

    if chat_completion.actionType == ActionType.REMOVE_BACKGROUND:
        # Handle background removal
        print("Removing background...")
        clipboard_data = monitor.get_last()
        if clipboard_data is None:
            print("No clipboard data found to remove background from.")
            return
        data_type, value = clipboard_data
        if data_type is None or value is None or data_type != "image":
            print("No image found in clipboard to remove background from.")
            return

        bg_removed_image = remove_background(value)
        monitor.copy_image(bg_removed_image)
        print("Background removed and copied to clipboard.")
        notify_user(chat_completion.message)
        return

    if chat_completion.actionType == ActionType.POST_TO_FACEBOOK:
        print("Posting images to Facebook...")
        images = monitor.get_images() or []
        if not images:
            last = monitor.get_last()
            if last and last[0] == 'image' and last[1] is not None:
                images = [last[1]]
        if not images:
            print("No images found in clipboard to post.")
            notify_user("No images in clipboard to post")
            return
        try:
            count = post_photos_to_page(images, caption=getattr(chat_completion, 'facebook_caption', None))
            if count > 0:
                notify_user(chat_completion.message or f"Posted {count} image(s) to Facebook")
            else:
                notify_user("Failed to post to Facebook")
        except Exception as e:
            print(f"Facebook post failed: {e}")
            notify_user("Facebook post failed")
        return

    if chat_completion.actionType == ActionType.ANALYZE_CHESS:
        try:
            analysis = analyze_chess_position(
                client_config.get("native"),
                user_hint=text,
            )
            best_move = analysis.best_move or "No legal move identified"
            move_description = analysis.move_description or best_move
            evaluation = analysis.evaluation or "No evaluation"
            explanation = analysis.explanation.strip()
            confidence = None
            raw_confidence = analysis.confidence
            if isinstance(raw_confidence, (int, float)):
                confidence = f"{raw_confidence * 100:.0f}% confidence"
            elif isinstance(raw_confidence, str):
                try:
                    confidence_val = float(raw_confidence)
                    confidence = f"{confidence_val * 100:.0f}% confidence"
                except ValueError:
                    confidence = raw_confidence.strip() or None

            summary_parts = [f"Best move (SAN): {best_move}"]
            if move_description and move_description != best_move:
                summary_parts.append(f"Move description: {move_description}")
            summary_parts.append(f"Evaluation: {evaluation}")
            if confidence:
                summary_parts.append(confidence)
            summary_parts.append(f"Explanation: {explanation}")
            summary_text = "\n".join(summary_parts)

            monitor.copy_text(summary_text)
            notify_user(move_description or f"Best move: {best_move}")
            print(f"Chess analysis:\n{summary_text}")
        except ChessAnalysisError as err:
            print(f"Chess analysis failed: {err}")
            notify_user("Chess analysis failed")
        except Exception as err:  # Catch-all to avoid crashing the assistant
            print(f"Unexpected chess analysis error: {err}")
            notify_user("Chess analysis failed")
        return

    if chat_completion.actionType == ActionType.SHORT_REPLY:
        if chat_completion.message:
            notify_user(chat_completion.message)
        return

def on_hotword_detected_with_memory(text, audio):
    # Get memory system instance
    memory = get_memory()
    
    # Get current clipboard content
    clipboard_data = monitor.get_last()
    if clipboard_data is None:
        clipboard_data_type, clipboard_content = None, None
        clipboard_str = "Empty"
    else:
        clipboard_data_type, clipboard_content = clipboard_data
        clipboard_str = f"Type: {clipboard_data_type}, Content: {str(clipboard_content)[:200]}..." if clipboard_content else "Empty"
    
    # Get relevant context from memory
    relevant_context = memory.get_relevant_context(text, max_entries=3)
    
    # Get current date/time
    from datetime import datetime
    current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    
    # Build the prompt with memory context
    prompt = f"Current Date/Time: {current_datetime}"
    prompt += f"\n\nVoice Command: {text}"
    prompt += "\n\n"
    prompt += "Current Clipboard Content:\n"
    prompt += clipboard_str
    prompt += "\n\n"
    # prompt += "Relevant Previous Context:\n"
    # prompt += relevant_context

    # Generate structured response
    try:
        chat_completion = client_config["instructor"].chat.completions.create(
            model=client_config["model"],
            response_model=AssistantResponse,
            messages=[
                {
                    "role": "system",
                    "content": f"""You are {hotword}, a voice assistant that responds to commands. You have access to several actions:

CURRENT CONTEXT: Use the provided current date/time to understand temporal references like "today", "tomorrow", "next week", "yesterday", etc.

MEMORY ACTIONS:
- SAVE_TO_MEMORY: Use when users explicitly want to save information for later recall
  * Keywords: "remember that", "save this", "note that", "keep in mind", "don't forget"
  * Types to use:
    - "preference": User likes/dislikes ("remember I like oat milk lattes")  
    - "important_info": Critical personal info ("note that I'm allergic to shellfish")
    - "reminder": Time-sensitive items ("remember my assignment is due next week")
    - "note": General facts ("save that the WiFi password is xyz123")
  * Example: "Remember that I have a sustainability assignment due next week" → SAVE_TO_MEMORY with type "reminder"

- SEARCH_MEMORY: Use when users want to find past information
  * Keywords: "what did we", "find that", "recall when", "search for", "do you remember"
  * Example: "What did we talk about yesterday?" → SEARCH_MEMORY with query "yesterday conversations"
  * Example: "Find that email address I saved" → SEARCH_MEMORY with query "email address"

OTHER ACTIONS:
- COPY_TEXT_TO_CLIPBOARD: Copy specific text user requests. This is your primary action for most commands, as the user will be asking you to help with their clipboard content.
- MAKE_MEME: Create meme from clipboard image with top/bottom text
- REMOVE_BACKGROUND: Remove background from clipboard image, making it transparent
- POST_TO_FACEBOOK: Post images from clipboard to your Facebook Page. Include an optional caption. Analyze the image to generate a caption that describes the image and will be included in the post. Generate engaging captions when the you want to sell an item. If the price is not provided, search the internet for typical pricing.
- ANALYZE_CHESS: Capture the current screen and analyze the visible chess position to recommend the strongest move when the user asks for chess help.
- SHORT_REPLY: Just notify user with a message. Their clipboard remains unchanged.

Use the current date/time to understand when things happened relative to now. Only respond with valid JSON matching the AssistantResponse schema.
Only choose ANALYZE_CHESS when the user clearly references playing chess or requests a best move recommendation.
If the user asks you to convert data formats, DO IT IN FULL DO NOT TRUNCATE ANYTHING. THERE IS NO CHARACTER LIMIT FOR COPIED CONTENT.
Remember that you are primariy interacting via short messages for quick confirmations, but when the user asks about code or technical behavior provide a medium-length explanation (a couple of sentences). There might be some noisey text from the voice recognition, so focus on the core intent of the command.
""",
                },
                {"role": "user", "content": prompt},
            ],
        )
        
        print(f"Raw response type: {type(chat_completion)}")
        print(f"Raw response actionType: {chat_completion.actionType} (type: {type(chat_completion.actionType)})")
        
    except Exception as e:
        print(f"Error during response generation: {e}")
        print(f"Error type: {type(e)}")
        # Create a fallback response
        chat_completion = AssistantResponse(
            thinking="Error occurred during response generation",
            actionType=ActionType.SHORT_REPLY,
            message="Sorry, I encountered an error processing your request."
        )

    # Store the interaction in memory
    action_type_str = chat_completion.actionType.value if chat_completion.actionType else "UNKNOWN"
    memory.store_interaction(
        voice_command=text,
        response=chat_completion.message,
        clipboard_content=clipboard_str if clipboard_content else None,
        action_type=action_type_str
    )

    # response = chat_completion.choices[0].message.content
    print(f"Full OpenAI response: {chat_completion}")
    if chat_completion.actionType == ActionType.NO_ACTION:
        if chat_completion.message:
            notify_user(chat_completion.message, emotion=chat_completion.emoji)
        print("No action needed.")
        return

    if (
        chat_completion.actionType == ActionType.COPY_TEXT_TO_CLIPBOARD
        and chat_completion.content_for_clipboard
    ):
        response = chat_completion.content_for_clipboard
        print(f"OpenAI response: {response}")
        # use instructor to remove fluff so response is only the text to copy to clipboard
        monitor.copy_text(response)
        print("Clipboard updated with OpenAI response.")
        notify_user(chat_completion.message, emotion=chat_completion.emoji)
        return

    if chat_completion.actionType == ActionType.MAKE_MEME:
        # Handle meme creation
        print("Creating meme...")
        clipboard_data = monitor.get_last()
        if clipboard_data is None:
            print("No clipboard data found to create a meme.")
            return
        data_type, value = clipboard_data
        if data_type is None or value is None or data_type != "image":
            print("No image found in clipboard to create a meme.")
            return
        from meme import make_meme

        meme_image = make_meme(
            value,
            upper_text=chat_completion.meme_top_text or "",
            lower_text=chat_completion.meme_bottom_text or "",
        )
        monitor.copy_image(meme_image)
        print("Meme created and copied to clipboard.")
        notify_user(chat_completion.message, emotion=chat_completion.emoji)
        return

    if chat_completion.actionType == ActionType.REMOVE_BACKGROUND:
        # Handle background removal
        print("Removing background...")
        clipboard_data = monitor.get_last()
        if clipboard_data is None:
            print("No clipboard data found to remove background from.")
            return
        data_type, value = clipboard_data
        if data_type is None or value is None or data_type != "image":
            print("No image found in clipboard to remove background from.")
            return

        bg_removed_image = remove_background(value)
        monitor.copy_image(bg_removed_image)
        print("Background removed and copied to clipboard.")
        notify_user(chat_completion.message, emotion=chat_completion.emoji)
        return

    if chat_completion.actionType == ActionType.POST_TO_FACEBOOK:
        print("Posting images to Facebook...")
        images = monitor.get_images() or []
        if not images:
            last = monitor.get_last()
            if last and last[0] == 'image' and last[1] is not None:
                images = [last[1]]
        if not images:
            print("No images found in clipboard to post.")
            notify_user("No images in clipboard to post", emotion=chat_completion.emoji)
            return
        try:
            count = post_photos_to_page(images, caption=getattr(chat_completion, 'facebook_caption', None))
            if count > 0:
                notify_user(chat_completion.message or f"Posted {count} image(s) to Facebook", emotion=chat_completion.emoji)
            else:
                notify_user("Failed to post to Facebook", emotion=chat_completion.emoji)
        except Exception as e:
            print(f"Facebook post failed: {e}")
            notify_user("Facebook post failed", emotion=chat_completion.emoji)
        return

    if chat_completion.actionType == ActionType.ANALYZE_CHESS:
        try:
            analysis = analyze_chess_position(
                client_config.get("native"),
                user_hint=text,
            )
            best_move = analysis.best_move or "No legal move identified"
            evaluation = analysis.evaluation or "No evaluation"
            explanation = analysis.explanation.strip()
            confidence = None
            raw_confidence = analysis.confidence
            if isinstance(raw_confidence, (int, float)):
                confidence = f"{raw_confidence * 100:.0f}% confidence"
            elif isinstance(raw_confidence, str):
                try:
                    confidence_val = float(raw_confidence)
                    confidence = f"{confidence_val * 100:.0f}% confidence"
                except ValueError:
                    confidence = raw_confidence.strip() or None

            summary_parts = [
                f"Best move: {best_move}",
                f"Evaluation: {evaluation}",
            ]
            if confidence:
                summary_parts.append(confidence)
            summary_parts.append(f"Explanation: {explanation}")
            summary_text = "\n".join(summary_parts)

            monitor.copy_text(summary_text)
            notify_user(f"Best move: {best_move}", emotion=chat_completion.emoji)
            print(f"Chess analysis:\n{summary_text}")

            # Store the analysis outcome in memory for future recall
            memory.store_interaction(
                voice_command=text,
                response=summary_text,
                clipboard_content=None,
                action_type="ANALYZE_CHESS",
            )
        except ChessAnalysisError as err:
            print(f"Chess analysis failed: {err}")
            notify_user("Chess analysis failed", emotion=chat_completion.emoji)
        except Exception as err:
            print(f"Unexpected chess analysis error: {err}")
            notify_user("Chess analysis failed", emotion=chat_completion.emoji)
        return

    if chat_completion.actionType == ActionType.SHORT_REPLY:
        if chat_completion.message:
            notify_user(chat_completion.message, emotion=chat_completion.emoji)
        return

    if chat_completion.actionType == ActionType.SEARCH_MEMORY:
        # Handle memory search - do a two-step process
        if chat_completion.memory_search_query:
            search_results = search_memory_tool(chat_completion.memory_search_query)
            print(f"Memory search results: {search_results}")
            
            # Now ask the LLM again with the search results to provide a proper answer
            follow_up_prompt = f"Current Date/Time: {current_datetime}"
            follow_up_prompt += f"\n\nOriginal Voice Command: {text}"
            follow_up_prompt += f"\n\nMemory Search Results:\n{search_results}"
            follow_up_prompt += f"\n\nPlease provide a helpful response based on the search results above. If the search results contain relevant information, use it to answer the user's question properly."
            
            follow_up_completion = client_config["instructor"].chat.completions.create(
                model=client_config["model"],
                response_model=AssistantResponse,
                messages=[
                    {
                        "role": "system",
                        "content": f"""You are {hotword}, providing information from memory search results.

TASK: Answer the user's original question using the search results provided.

ACTIONS AVAILABLE:
- COPY_TEXT_TO_CLIPBOARD: If user wants specific information copied (emails, passwords, addresses, etc.)
- SHORT_REPLY: For conversational responses about what you found
- NO_ACTION: If no specific action is needed

INSTRUCTIONS:
- If search results are empty/irrelevant: "I couldn't find any relevant information about [topic]"  
- If results contain useful info: Summarize what you found and offer to copy specific details if helpful
- Be conversational and helpful - don't just list raw search results
- Provide medium-length explanations (a couple of sentences) when describing code or technical findings; the app will handle notification truncation automatically.""",
                    },
                    {"role": "user", "content": follow_up_prompt},
                ],
            )
            
            print(f"Follow-up response: {follow_up_completion}")
            
            # Handle the follow-up response
            if follow_up_completion.actionType == ActionType.COPY_TEXT_TO_CLIPBOARD and follow_up_completion.content_for_clipboard:
                monitor.copy_text(follow_up_completion.content_for_clipboard)
                notify_user(follow_up_completion.message, emotion=follow_up_completion.emoji)
            elif follow_up_completion.actionType == ActionType.SHORT_REPLY:
                notify_user(follow_up_completion.message, emotion=follow_up_completion.emoji)
            
            # Store the memory search interaction
            memory.store_interaction(
                voice_command=text,
                response=follow_up_completion.message,
                clipboard_content=search_results,
                action_type="MEMORY_SEARCH_FOLLOWUP"
            )
        return

    if chat_completion.actionType == ActionType.SAVE_TO_MEMORY:
        # Handle saving important information to memory
        if chat_completion.memory_save_content:
            memory_type = chat_completion.memory_save_type or "user_note"
            metadata = {
                "original_command": text,
                "save_type": memory_type
            }
            # Use explicit memory storage method for ChromaDB
            entry_id = memory.store_explicit_memory(memory_type, chat_completion.memory_save_content, metadata)
            print(f"Saved to memory with ID: {entry_id}")
            notify_user(chat_completion.message, emotion=chat_completion.emoji)
        return


if __name__ == "__main__":
    listener = HotwordListener(hotword, on_hotword_detected)
    listener.start(background=True)

    # Keep the main thread alive to allow background listening
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping listener...")
        listener.stop()
