import struct
import pvporcupine
import sounddevice as sd
import os
from dotenv import load_dotenv

# Create a Porcupine wake word detector
# porcupine = pvporcupine.create(keywords=["porcupine"])

# # Path to your custom wake word model (.ppn file)
# WAKEWORD_PATH = "my_custom_kiwi.ppn"
load_dotenv()
ACCESS_KEY = os.getenv("ACCESS_KEY")
# # # Initialize Porcupine with built-in wake word
sara = pvporcupine.create(
    access_key=ACCESS_KEY, keywords=["porcupine"]
)


def audio_callback(indata, frames, time, status):
    if status:
        print(status)

    # Convert audio stream to the right format
    pcm = struct.unpack_from("h" * sara.frame_length, indata)
    result = sara.process(pcm)

    if result >= 0:
        print("Wake word 'porcupine' detected!")


# Open a stream from the microphone
with sd.RawInputStream(
    samplerate=sara.sample_rate,
    blocksize=sara.frame_length,
    dtype="int16",
    channels=1,
    callback=audio_callback,
):
    print("Listening for 'porcupine'... Press Ctrl+C to stop.")
    while True:
        sd.sleep(1000)
